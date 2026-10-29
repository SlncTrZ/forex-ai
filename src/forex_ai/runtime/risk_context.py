from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from forex_ai.journal.db import session
from forex_ai.mt5.contracts import BrokerState
from forex_ai.risk.broker_engine import ExistingPosition, RiskContext

D = Decimal
UTC = timezone.utc


def _period_start(now_utc: datetime) -> tuple[datetime, datetime]:
    now = now_utc.astimezone(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    return day, week


def _reference_equity(con, start_utc: datetime, login: int | None = None) -> Decimal | None:
    if login is not None:
        row = con.execute(
            "SELECT equity FROM accounts WHERE timestamp>=? AND login=? AND equity IS NOT NULL AND equity > 0 ORDER BY timestamp ASC LIMIT 1",
            (start_utc.isoformat(), login),
        ).fetchone()
        if row is not None and row[0] is not None:
            val = D(str(row[0]))
            if val > 0:
                return val
        return None

    row = con.execute(
        "SELECT equity FROM accounts WHERE timestamp>=? AND equity IS NOT NULL AND equity > 0 ORDER BY timestamp ASC LIMIT 1",
        (start_utc.isoformat(),),
    ).fetchone()
    if row is not None and row[0] is not None:
        val = D(str(row[0]))
        if val > 0:
            return val
    return None


def _realized_loss(con, start_utc: datetime) -> Decimal:
    row = con.execute(
        """SELECT COALESCE(SUM(CASE WHEN net < 0 THEN -net ELSE 0 END),0) FROM (
               SELECT COALESCE(profit,0)+COALESCE(commission,0)+COALESCE(swap,0)+COALESCE(fee,0) AS net
               FROM mt5_deals WHERE time>=? AND type IN (0, 1)
           )""",
        (int(start_utc.timestamp()),),
    ).fetchone()
    return D(str(row[0] or 0))


def _net_cash_flow(con, start_utc: datetime) -> Decimal:
    row = con.execute(
        "SELECT COALESCE(SUM(profit), 0) FROM mt5_deals WHERE time>=? AND type = 2",
        (int(start_utc.timestamp()),),
    ).fetchone()
    return D(str(row[0] or 0))


def build_risk_context(db_path: Path, broker: BrokerState, *, now_utc: datetime) -> RiskContext:
    """Build conservative broker+journal risk state for production V1 decisions.

    Pending broker orders are deliberately treated as unquantified active exposure
    until their full direction/protection semantics can be reconstructed safely.
    That makes the deterministic engine fail closed instead of undercounting risk.
    """
    day_start, week_start = _period_start(now_utc)
    login = int(broker.account.login) if broker.account and broker.account.login else None
    current_equity = D(str(broker.account.equity)) if broker.account and broker.account.equity > 0 else D("0")
    with session(db_path) as con:
        ref_day = _reference_equity(con, day_start, login=login)
        daily_reference = ref_day if (ref_day is not None and ref_day > 0) else current_equity

        ref_week = _reference_equity(con, week_start, login=login)
        weekly_reference = ref_week if (ref_week is not None and ref_week > 0) else current_equity

        daily_loss = _realized_loss(con, day_start)
        weekly_loss = _realized_loss(con, week_start)
        daily_cash = _net_cash_flow(con, day_start)
        weekly_cash = _net_cash_flow(con, week_start)
        if login is not None:
            peak_row = con.execute(
                "SELECT MAX(equity) FROM accounts WHERE timestamp>=? AND login=? AND equity IS NOT NULL",
                (week_start.isoformat(), login),
            ).fetchone()
        else:
            peak_row = con.execute(
                "SELECT MAX(equity) FROM accounts WHERE timestamp>=? AND equity IS NOT NULL",
                (week_start.isoformat(),),
            ).fetchone()
    raw_peak = current_equity if (peak_row is None or peak_row[0] is None or D(str(peak_row[0])) <= 0) else max(current_equity, D(str(peak_row[0])))
    adjusted_peak = max(current_equity, raw_peak + weekly_cash)
    drawdown = max(D("0"), adjusted_peak - current_equity)

    if daily_reference <= 0 and current_equity > 0:
        daily_reference = current_equity
    if weekly_reference <= 0 and current_equity > 0:
        weekly_reference = current_equity

    existing = tuple(
        ExistingPosition(intent_id=f"broker-position:{position.ticket}", position=position)
        for position in broker.positions
    )
    active_ids = [item.intent_id for item in existing]
    active_ids.extend(f"broker-order:{order.ticket}" for order in broker.pending_orders)

    tick_age = D("0")
    if broker.ticks:
        newest_capture = max(tick.captured_at_utc for tick in broker.ticks)
        tick_age = max(D("0"), D(str((now_utc.astimezone(UTC) - newest_capture).total_seconds())))

    return RiskContext(
        active_intent_ids=tuple(active_ids),
        existing_positions=existing,
        tick_age_seconds=tick_age,
        daily_realized_loss_amount=daily_loss,
        weekly_realized_loss_amount=weekly_loss,
        drawdown_amount=drawdown,
        daily_reference_equity=daily_reference,
        weekly_reference_equity=weekly_reference,
        daily_net_cash_flow=daily_cash,
        weekly_net_cash_flow=weekly_cash,
    )
