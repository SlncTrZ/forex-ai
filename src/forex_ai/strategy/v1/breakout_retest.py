from __future__ import annotations

from datetime import datetime, timedelta

from forex_ai.market.indicators import atr
from forex_ai.strategy.config import bundled_strategy_config

from .contracts import DecisionEvidence, Invalidation, MarketSnapshot, StrategyConfig, StrategyResult, build_candidate

DEFAULT_CONFIG = bundled_strategy_config("breakout_retest_v1")


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
    p = config.parameters
    timeframe = str(p["decision_timeframe"])
    tf = snapshot.timeframes.get(timeframe)
    required = max(
        int(p["range_bars"]) + int(p["breakout_search_bars"]) + 2,
        int(p["atr_period"]) + 2,
    )
    if tf is None:
        evidence = DecisionEvidence(("MISSING_TIMEFRAME",), {"missing": (timeframe,)})
        return StrategyResult(None, None, evidence, ("MISSING_TIMEFRAME",))
    if len(tf.closed_bars) < required:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {"timeframe": timeframe, "required": required})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest_index = len(bars) - 1
    latest = bars[latest_index]
    atr_period = int(p["atr_period"])
    current_atr = atr(bars, atr_period)
    if current_atr <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {"atr": current_atr})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    range_bars = int(p["range_bars"])
    first_breakout_index = max(range_bars, latest_index - int(p["breakout_search_bars"]))
    for breakout_index in range(latest_index - 2, first_breakout_index - 1, -1):
        prior = bars[breakout_index - range_bars:breakout_index]
        prior_atr = atr(bars[:breakout_index], atr_period)
        if prior_atr <= 0:
            continue
        upper = max(bar.high for bar in prior)
        lower = min(bar.low for bar in prior)
        breakout = bars[breakout_index]
        min_break = float(p["min_breakout_close_atr"]) * prior_atr
        buy_break = breakout.close > upper + min_break
        sell_break = breakout.close < lower - min_break
        if buy_break == sell_break:
            continue

        retest_bars = bars[breakout_index + 1:latest_index]
        if not retest_bars:
            continue
        tolerance = float(p["retest_tolerance_atr"]) * current_atr
        confirmation = float(p["confirmation_buffer_atr"]) * current_atr

        if buy_break:
            retest = [bar for bar in retest_bars if upper - tolerance <= bar.low <= upper + tolerance]
            if not retest or latest.close <= upper + confirmation:
                continue
            side = "BUY"
            entry = snapshot.ask
            stop = min(bar.low for bar in retest_bars) - float(p["stop_buffer_atr"]) * current_atr
            retest_depth = (min(bar.low for bar in retest_bars) - upper) / current_atr
            breakout_strength = (breakout.close - upper) / prior_atr
            boundary = upper
        else:
            retest = [bar for bar in retest_bars if lower - tolerance <= bar.high <= lower + tolerance]
            if not retest or latest.close >= lower - confirmation:
                continue
            side = "SELL"
            entry = snapshot.bid
            stop = max(bar.high for bar in retest_bars) + float(p["stop_buffer_atr"]) * current_atr
            retest_depth = (lower - max(bar.high for bar in retest_bars)) / current_atr
            breakout_strength = (lower - breakout.close) / prior_atr
            boundary = lower

        risk = abs(entry - stop)
        if risk <= 0 or (side == "BUY" and stop >= entry) or (side == "SELL" and stop <= entry):
            evidence = DecisionEvidence(("INVALID_STOP_GEOMETRY",), {"entry": entry, "stop": stop})
            return StrategyResult(None, None, evidence, ("INVALID_STOP_GEOMETRY",))

        target_r = float(p["target_r"])
        target = entry + risk * target_r if side == "BUY" else entry - risk * target_r
        evidence = DecisionEvidence(
            ("BREAKOUT_RETEST_CONFIRMED",),
            {
                "side": side,
                "timeframe": timeframe,
                "range_boundary": boundary,
                "breakout_strength_atr": breakout_strength,
                "bars_since_breakout": latest_index - breakout_index,
                "retest_depth_atr": retest_depth,
                "confirmation_distance_atr": (
                    (latest.close - boundary) / current_atr
                    if side == "BUY"
                    else (boundary - latest.close) / current_atr
                ),
                "atr": current_atr,
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
            Invalidation("RETEST_STRUCTURE_BREAK", stop, "Breakout-retest structure plus ATR buffer"),
            evidence,
        )

    evidence = DecisionEvidence(("NO_BREAKOUT_RETEST",), {})
    return StrategyResult(None, None, evidence, ("NO_BREAKOUT_RETEST",))
