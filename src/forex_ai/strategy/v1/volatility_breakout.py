from __future__ import annotations

from datetime import datetime, timedelta

from forex_ai.strategy.config import bundled_strategy_config

from .contracts import DecisionEvidence, Invalidation, MarketSnapshot, StrategyConfig, StrategyResult, build_candidate
from .indicators import atr, efficiency, trend_state

DEFAULT_CONFIG = bundled_strategy_config("volatility_breakout_v1")


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
    p = config.parameters
    tf = snapshot.timeframes.get("M15")
    range_bars = int(p["range_bars"])
    atr_period = int(p["atr_period"])
    if not tf or len(tf.closed_bars) < max(range_bars + 2, atr_period + 2):
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prior = bars[-(range_bars + 1):-1]
    prior_high = max(bar.high for bar in prior)
    prior_low = min(bar.low for bar in prior)
    atr_prior = atr(bars[:-1], atr_period)
    expansion = 0.0 if atr_prior <= 0 else (latest.high - latest.low) / atr_prior

    side = "BUY" if latest.close > prior_high else "SELL" if latest.close < prior_low else None
    if side is None:
        evidence = DecisionEvidence(("NO_RANGE_BREAK",), {"prior_high": prior_high, "prior_low": prior_low, "close": latest.close})
        return StrategyResult(None, None, evidence, ("NO_RANGE_BREAK",))
    if expansion < float(p["min_expansion"]):
        evidence = DecisionEvidence(("NO_VOLATILITY_EXPANSION",), {"expansion": expansion})
        return StrategyResult(None, None, evidence, ("NO_VOLATILITY_EXPANSION",))

    trend_efficiency = efficiency(bars[:-1], int(p["efficiency_window"]))
    trend = trend_state(bars[:-1], int(p["trend_ema_fast"]), int(p["trend_ema_slow"]))
    wanted = "UP" if side == "BUY" else "DOWN"
    if trend_efficiency < float(p["min_efficiency"]) or trend not in {wanted, "MIXED"}:
        evidence = DecisionEvidence(("TREND_STRENGTH_REJECT",), {"efficiency": trend_efficiency, "trend": trend, "side": side})
        return StrategyResult(None, None, evidence, ("TREND_STRENGTH_REJECT",))

    boundary = prior_high if side == "BUY" else prior_low
    extension_atr = abs(latest.close - boundary) / (atr_prior or 1.0)
    if extension_atr > float(p["max_extension_atr"]):
        evidence = DecisionEvidence(("OVEREXTENDED_BREAKOUT",), {"extension_atr": extension_atr})
        return StrategyResult(None, None, evidence, ("OVEREXTENDED_BREAKOUT",))

    total_cost = snapshot.spread_cost + snapshot.commission_cost
    if atr_prior > 0 and total_cost / atr_prior > float(p["max_cost_atr"]):
        evidence = DecisionEvidence(("COST_TOO_HIGH",), {"cost_atr": total_cost / atr_prior})
        return StrategyResult(None, None, evidence, ("COST_TOO_HIGH",))

    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = float(p["stop_buffer_atr"]) * atr_prior
    stop = prior_low - buffer if side == "BUY" else prior_high + buffer
    risk = abs(entry - stop)
    target_r = float(p["target_r"])
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r
    evidence = DecisionEvidence(
        ("VOLATILITY_BREAKOUT_CONFIRMED",),
        {"side": side, "prior_high": prior_high, "prior_low": prior_low, "atr": atr_prior, "expansion": expansion,
         "efficiency": trend_efficiency, "cost": total_cost},
    )
    expiry = now_utc + timedelta(minutes=int(p["expiry_minutes"]))
    candidate = build_candidate(
        snapshot=snapshot, config=config, side=side, entry=entry, stop_loss=stop, take_profit=target,
        generated_at_utc=now_utc, expires_at_utc=expiry, evidence=evidence,
    )
    return StrategyResult(candidate, Invalidation("RANGE_REENTRY", stop, "Breakout invalidation beyond prior range"), evidence)
