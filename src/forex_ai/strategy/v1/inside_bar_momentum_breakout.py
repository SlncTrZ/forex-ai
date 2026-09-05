from __future__ import annotations

from datetime import datetime, timedelta

from forex_ai.market.candles import candle_shape, is_inside_bar
from forex_ai.market.indicators import atr
from forex_ai.strategy.config import bundled_strategy_config

from .contracts import DecisionEvidence, Invalidation, MarketSnapshot, StrategyConfig, StrategyResult, build_candidate

DEFAULT_CONFIG = bundled_strategy_config("inside_bar_momentum_breakout_v1")


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
    p = config.parameters
    timeframe = str(p["decision_timeframe"])
    tf = snapshot.timeframes.get(timeframe)
    required = max(int(p["atr_period"]) + 1, 3)
    if tf is None:
        evidence = DecisionEvidence(("MISSING_TIMEFRAME",), {"missing": (timeframe,)})
        return StrategyResult(None, None, evidence, ("MISSING_TIMEFRAME",))
    if len(tf.closed_bars) < required:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {"timeframe": timeframe, "required": required})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    mother, inside, trigger = bars[-3], bars[-2], bars[-1]
    if not is_inside_bar(mother, inside):
        evidence = DecisionEvidence(("NO_INSIDE_BAR",), {})
        return StrategyResult(None, None, evidence, ("NO_INSIDE_BAR",))

    atr_value = atr(bars, int(p["atr_period"]))
    if atr_value <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {"atr": atr_value})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    mother_shape = candle_shape(mother)
    mother_range_atr = mother_shape.range / atr_value
    if mother_range_atr < float(p["mother_min_range_atr"]):
        evidence = DecisionEvidence(("MOTHER_RANGE_TOO_SMALL",), {"mother_range_atr": mother_range_atr})
        return StrategyResult(None, None, evidence, ("MOTHER_RANGE_TOO_SMALL",))
    if mother_shape.body_ratio < float(p["mother_min_body_ratio"]):
        evidence = DecisionEvidence(("MOTHER_BODY_TOO_SMALL",), {"mother_body_ratio": mother_shape.body_ratio})
        return StrategyResult(None, None, evidence, ("MOTHER_BODY_TOO_SMALL",))

    breakout_buffer = float(p["breakout_buffer_atr"]) * atr_value
    buy_break = trigger.close > mother.high + breakout_buffer
    sell_break = trigger.close < mother.low - breakout_buffer
    if bool(p["require_mother_direction"]):
        buy_break = buy_break and mother_shape.direction == "BULL"
        sell_break = sell_break and mother_shape.direction == "BEAR"
    if buy_break == sell_break:
        evidence = DecisionEvidence(
            ("NO_VALID_BREAKOUT",),
            {"buy_break": buy_break, "sell_break": sell_break, "mother_direction": mother_shape.direction},
        )
        return StrategyResult(None, None, evidence, ("NO_VALID_BREAKOUT",))

    side = "BUY" if buy_break else "SELL"
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    stop_buffer = float(p["stop_buffer_atr"]) * atr_value
    stop = inside.low - stop_buffer if side == "BUY" else inside.high + stop_buffer
    risk = abs(entry - stop)
    if risk <= 0 or (side == "BUY" and stop >= entry) or (side == "SELL" and stop <= entry):
        evidence = DecisionEvidence(("INVALID_STOP_GEOMETRY",), {"entry": entry, "stop": stop})
        return StrategyResult(None, None, evidence, ("INVALID_STOP_GEOMETRY",))

    target_r = float(p["target_r"])
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r
    evidence = DecisionEvidence(
        ("INSIDE_BAR_MOMENTUM_CONFIRMED",),
        {
            "side": side,
            "timeframe": timeframe,
            "mother_range_atr": mother_range_atr,
            "mother_body_ratio": mother_shape.body_ratio,
            "mother_direction": mother_shape.direction,
            "inside_range_atr": (inside.high - inside.low) / atr_value,
            "breakout_distance_atr": (
                (trigger.close - mother.high) / atr_value
                if side == "BUY"
                else (mother.low - trigger.close) / atr_value
            ),
            "atr": atr_value,
        },
    )
    expiry = now_utc + timedelta(minutes=int(p["expiry_minutes"]))
    candidate = build_candidate(
        snapshot=snapshot,
        config=config,
        side=side,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        generated_at_utc=now_utc,
        expires_at_utc=expiry,
        evidence=evidence,
        decision_timeframe=timeframe,
    )
    return StrategyResult(
        candidate,
        Invalidation("INSIDE_BAR_STRUCTURE_BREAK", stop, "Inside-bar structure plus ATR buffer"),
        evidence,
    )
