from __future__ import annotations

from datetime import datetime, timedelta

from .contracts import DecisionEvidence, Invalidation, MarketSnapshot, StrategyConfig, StrategyResult, StrategyVersion, build_candidate
from .indicators import atr, efficiency, trend_state

DEFAULT_CONFIG = StrategyConfig(
    StrategyVersion("volatility_breakout_v1", "1.0.0"),
    {
        "range_bars": 20,
        "atr_period": 14,
        "min_expansion": 1.2,
        "min_efficiency": 0.30,
        "max_extension_atr": 1.25,
        "stop_buffer_atr": 0.20,
        "target_r": 2.0,
        "expiry_minutes": 30,
        "max_cost_atr": 0.15,
    },
)


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
    p = config.parameters
    tf = snapshot.timeframes.get("M15")
    range_bars = int(p.get("range_bars", 20))
    atr_period = int(p.get("atr_period", 14))
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
    if expansion < float(p.get("min_expansion", 1.2)):
        evidence = DecisionEvidence(("NO_VOLATILITY_EXPANSION",), {"expansion": expansion})
        return StrategyResult(None, None, evidence, ("NO_VOLATILITY_EXPANSION",))

    trend_efficiency = efficiency(bars[:-1], 14)
    trend = trend_state(bars[:-1])
    wanted = "UP" if side == "BUY" else "DOWN"
    if trend_efficiency < float(p.get("min_efficiency", 0.30)) or trend not in {wanted, "MIXED"}:
        evidence = DecisionEvidence(("TREND_STRENGTH_REJECT",), {"efficiency": trend_efficiency, "trend": trend, "side": side})
        return StrategyResult(None, None, evidence, ("TREND_STRENGTH_REJECT",))

    boundary = prior_high if side == "BUY" else prior_low
    extension_atr = abs(latest.close - boundary) / (atr_prior or 1.0)
    if extension_atr > float(p.get("max_extension_atr", 1.25)):
        evidence = DecisionEvidence(("OVEREXTENDED_BREAKOUT",), {"extension_atr": extension_atr})
        return StrategyResult(None, None, evidence, ("OVEREXTENDED_BREAKOUT",))

    total_cost = snapshot.spread_cost + snapshot.commission_cost
    if atr_prior > 0 and total_cost / atr_prior > float(p.get("max_cost_atr", 0.15)):
        evidence = DecisionEvidence(("COST_TOO_HIGH",), {"cost_atr": total_cost / atr_prior})
        return StrategyResult(None, None, evidence, ("COST_TOO_HIGH",))

    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = float(p.get("stop_buffer_atr", 0.20)) * atr_prior
    stop = prior_low - buffer if side == "BUY" else prior_high + buffer
    risk = abs(entry - stop)
    target_r = float(p.get("target_r", 2.0))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r
    evidence = DecisionEvidence(
        ("VOLATILITY_BREAKOUT_CONFIRMED",),
        {"side": side, "prior_high": prior_high, "prior_low": prior_low, "atr": atr_prior, "expansion": expansion,
         "efficiency": trend_efficiency, "cost": total_cost},
    )
    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 30)))
    candidate = build_candidate(
        snapshot=snapshot, config=config, side=side, entry=entry, stop_loss=stop, take_profit=target,
        generated_at_utc=now_utc, expires_at_utc=expiry, evidence=evidence,
    )
    return StrategyResult(candidate, Invalidation("RANGE_REENTRY", stop, "Breakout invalidation beyond prior range"), evidence)
