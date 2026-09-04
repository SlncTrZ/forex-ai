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


def _reference_equity(con, start_utc: datetime) -> Decimal | None:
    row = con.execute(
        "SELECT equity FROM accounts WHERE timestamp>=? AND equity IS NOT NULL ORDER BY timestamp ASC LIMIT 1",
        (start_utc.isoformat(),),
    ).fetchone()
    return None if row is None else D(str(row[0]))


def _realized_loss(con, start_utc: datetime) -> Decimal:
    row = con.execute(
        """SELECT COALESCE(SUM(CASE WHEN net < 0 THEN -net ELSE 0 END),0) FROM (
               SELECT COALESCE(profit,0)+COALESCE(commission,0)+COALESCE(swap,0)+COALESCE(fee,0) AS net
               FROM mt5_deals WHERE time>=?
           )""",
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
    with session(db_path) as con:
        daily_reference = _reference_equity(con, day_start)
        weekly_reference = _reference_equity(con, week_start)
        daily_loss = _realized_loss(con, day_start)
        weekly_loss = _realized_loss(con, week_start)
        peak_row = con.execute(
            "SELECT MAX(equity) FROM accounts WHERE timestamp>=? AND equity IS NOT NULL",
            (week_start.isoformat(),),
        ).fetchone()
    current_equity = D(str(broker.account.equity))
    peak = current_equity if peak_row is None or peak_row[0] is None else D(str(peak_row[0]))
    drawdown = max(D("0"), peak - current_equity)

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
    )
