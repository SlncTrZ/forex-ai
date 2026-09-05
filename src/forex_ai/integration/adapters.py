from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping

from forex_ai.mt5.contracts import AccountSnapshot, Bar, BrokerPosition, SymbolContract, TickSnapshot
from forex_ai.risk.broker_engine import CandidateInput, ExistingPosition
from forex_ai.strategy.v1.contracts import Candle, CandidateEnvelope, MarketSnapshot, TimeframeSnapshot


def _utc_from_epoch(seconds: int | float) -> datetime:
    return datetime.fromtimestamp(float(seconds), timezone.utc)


def account_snapshot(raw: Mapping[str, Any], *, captured_at_utc: datetime) -> AccountSnapshot:
    return AccountSnapshot(
        login=int(raw["login"]),
        server=str(raw["server"]),
        currency=str(raw["currency"]),
        balance=float(raw["balance"]),
        equity=float(raw["equity"]),
        margin=float(raw.get("margin") or 0.0),
        margin_free=float(raw.get("margin_free") or 0.0),
        leverage=int(raw["leverage"]),
        captured_at_utc=captured_at_utc,
    )


def symbol_contract(
    raw: Mapping[str, Any],
    *,
    symbol: str | None = None,
    trade_allowed: bool,
    market_orders_allowed: bool,
    session_open: bool,
) -> SymbolContract:
    name = symbol or str(raw["name"])
    return SymbolContract(
        symbol=name,
        digits=int(raw["digits"]),
        point=float(raw["point"]),
        trade_contract_size=float(raw["trade_contract_size"]),
        trade_tick_size=float(raw["trade_tick_size"]) if raw.get("trade_tick_size") else None,
        volume_min=float(raw["volume_min"]),
        volume_max=float(raw["volume_max"]),
        volume_step=float(raw["volume_step"]),
        trade_stops_level=int(raw.get("trade_stops_level") or 0),
        trade_freeze_level=int(raw.get("trade_freeze_level") or 0),
        trade_mode=int(raw["trade_mode"]) if raw.get("trade_mode") is not None else None,
        order_mode=int(raw["order_mode"]) if raw.get("order_mode") is not None else None,
        filling_mode=int(raw["filling_mode"]) if raw.get("filling_mode") is not None else None,
        trade_allowed=trade_allowed,
        market_orders_allowed=market_orders_allowed,
        session_open=session_open,
        currency_base=str(raw["currency_base"]) if raw.get("currency_base") else None,
        currency_profit=str(raw["currency_profit"]) if raw.get("currency_profit") else None,
        currency_margin=str(raw["currency_margin"]) if raw.get("currency_margin") else None,
    )


def tick_snapshot(raw: Mapping[str, Any], *, symbol: str, captured_at_utc: datetime) -> TickSnapshot:
    time_msc = raw.get("time_msc")
    if time_msc is None and raw.get("time") is not None:
        time_msc = int(raw["time"]) * 1000
    return TickSnapshot(
        symbol=symbol,
        bid=float(raw["bid"]),
        ask=float(raw["ask"]),
        time_msc=int(time_msc),
        captured_at_utc=captured_at_utc,
    )


def _candle(raw: Mapping[str, Any]) -> Candle:
    return Candle(
        time_utc=_utc_from_epoch(raw["time"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        volume=float(raw.get("tick_volume") or raw.get("real_volume") or 0.0),
    )


def timeframe_snapshot(timeframe: str, raw_bars: Iterable[Mapping[str, Any]]) -> TimeframeSnapshot:
    """Convert MT5 bars while treating the newest returned bar as forming/current.

    This preserves the closed-candle invariant even when copy_rates_from_pos starts
    from position zero and therefore includes the currently forming candle.
    """
    candles = tuple(_candle(row) for row in raw_bars)
    if not candles:
        return TimeframeSnapshot(timeframe, ())
    return TimeframeSnapshot(timeframe, candles[:-1], candles[-1])


def market_snapshot(
    *,
    symbol: str,
    tick: TickSnapshot,
    captured_at_utc: datetime,
    timeframes: Mapping[str, TimeframeSnapshot],
    spread_cost: float = 0.0,
    commission_cost: float = 0.0,
    metadata: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        captured_at_utc=captured_at_utc,
        market_time_msc=tick.time_msc,
        bid=tick.bid,
        ask=tick.ask,
        timeframes=timeframes,
        spread_cost=spread_cost,
        commission_cost=commission_cost,
        metadata=metadata or {},
        context=context or {},
    )


def candidate_input(candidate: CandidateEnvelope, *, now_utc: datetime) -> CandidateInput:
    generated = candidate.generated_at_utc.astimezone(timezone.utc)
    now = now_utc.astimezone(timezone.utc)
    age_seconds = max(Decimal("0"), Decimal(str((now - generated).total_seconds())))
    return CandidateInput(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        side=candidate.side,
        reference_entry=Decimal(str(candidate.reference_entry)),
        stop_loss=Decimal(str(candidate.stop_loss)),
        take_profit=Decimal(str(candidate.take_profit)),
        expires_at_utc=candidate.expires_at_utc,
        age_seconds=age_seconds,
    )


def broker_position(
    raw: Mapping[str, Any],
    *,
    buy_type: int,
    sell_type: int,
) -> BrokerPosition:
    position_type = int(raw["type"])
    if position_type == buy_type:
        side = "BUY"
    elif position_type == sell_type:
        side = "SELL"
    else:
        raise ValueError(f"unsupported MT5 position type {position_type}")
    return BrokerPosition(
        ticket=int(raw["ticket"]),
        symbol=str(raw["symbol"]),
        side=side,
        volume=float(raw["volume"]),
        price_open=float(raw["price_open"]),
        price_current=float(raw["price_current"]),
        sl=float(raw.get("sl") or 0.0),
        tp=float(raw.get("tp") or 0.0),
        profit=float(raw.get("profit") or 0.0),
        magic=int(raw["magic"]) if raw.get("magic") is not None else None,
        comment=str(raw.get("comment") or ""),
    )


def existing_positions(
    raw_positions: Iterable[Mapping[str, Any]],
    *,
    buy_type: int,
    sell_type: int,
    intent_id_for: Callable[[Mapping[str, Any]], str],
    correlation_group_for: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> tuple[ExistingPosition, ...]:
    rows: list[ExistingPosition] = []
    for raw in raw_positions:
        rows.append(
            ExistingPosition(
                intent_id=intent_id_for(raw),
                position=broker_position(raw, buy_type=buy_type, sell_type=sell_type),
                correlation_group=correlation_group_for(raw) if correlation_group_for else None,
            )
        )
    return tuple(rows)
