from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite

from .contracts import DecisionEvidence, Invalidation, MarketSnapshot, StrategyConfig, StrategyResult, StrategyVersion, build_candidate
from .indicators import atr, efficiency, ema, trend_state

TREND_CONFIG = StrategyConfig(
    StrategyVersion("exploration_trend_v1", "1.0.0"),
    {
        "ema_fast": 20,
        "ema_slow": 50,
        "pullback_atr": 0.75,
        "volatility_buffer_atr": 0.25,
        "target_r": 2.0,
        "expiry_minutes": 45,
        "probe_distance_atr": 1.25,
    },
)

BREAKOUT_CONFIG = StrategyConfig(
    StrategyVersion("exploration_breakout_v1", "1.0.0"),
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


def _normalized(value: float, denominator: float) -> float:
    if denominator <= 0 or not isfinite(value):
        return 0.0
    return value / denominator


def _trend_side(h4_state: str, h1_state: str) -> tuple[str | None, str]:
    if h4_state in {"UP", "DOWN"}:
        opposite = "DOWN" if h4_state == "UP" else "UP"
        if h1_state == opposite:
            return None, "CONFLICTING_REGIME"
        return ("BUY" if h4_state == "UP" else "SELL"), "H4_LED"
    if h4_state == "MIXED" and h1_state in {"UP", "DOWN"}:
        return ("BUY" if h1_state == "UP" else "SELL"), "H1_LED"
    return None, "NO_DIRECTIONAL_THESIS"


def evaluate_trend(snapshot: MarketSnapshot, config: StrategyConfig = TREND_CONFIG, now_utc: datetime | None = None) -> StrategyResult:
    now = now_utc or snapshot.captured_at_utc
    p = config.parameters
    missing = tuple(tf for tf in ("H4", "H1", "M15") if tf not in snapshot.timeframes)
    if missing:
        evidence = DecisionEvidence(("MISSING_TIMEFRAME",), {"missing": missing})
        return StrategyResult(None, None, evidence, ("MISSING_TIMEFRAME",))

    h4, h1, m15 = (snapshot.timeframes[name].closed_bars for name in ("H4", "H1", "M15"))
    slow = int(p.get("ema_slow", 50))
    fast = int(p.get("ema_fast", 20))
    if min(len(h4), len(h1), len(m15)) < slow:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    h4_state = trend_state(h4, fast, slow)
    h1_state = trend_state(h1, fast, slow)
    side, thesis_source = _trend_side(h4_state, h1_state)
    if side is None:
        evidence = DecisionEvidence((thesis_source,), {"h4": h4_state, "h1": h1_state})
        return StrategyResult(None, None, evidence, (thesis_source,))

    closes = [bar.close for bar in m15]
    atr_m15 = atr(m15, 14)
    if atr_m15 <= 0:
        evidence = DecisionEvidence(("ATR_UNAVAILABLE",), {"atr_m15": atr_m15})
        return StrategyResult(None, None, evidence, ("ATR_UNAVAILABLE",))

    ema_fast_now = ema(closes, fast)
    ema_slow_now = ema(closes, slow)
    ema_fast_prev = ema(closes[:-1], fast) if len(closes) > fast else ema_fast_now
    ema_slow_prev = ema(closes[:-1], slow) if len(closes) > slow else ema_slow_now
    prev, latest = m15[-2], m15[-1]
    allowance = float(p.get("pullback_atr", 0.75)) * atr_m15

    if side == "BUY":
        pulled_back = prev.low <= ema_fast_now + allowance
        reclaimed = latest.close > ema_fast_now and latest.close > prev.close
        directional_continue = latest.close > prev.close
        pullback_depth_atr = _normalized(max(0.0, ema_fast_now - prev.low), atr_m15)
        reclaim_strength_atr = _normalized(latest.close - ema_fast_now, atr_m15)
    else:
        pulled_back = prev.high >= ema_fast_now - allowance
        reclaimed = latest.close < ema_fast_now and latest.close < prev.close
        directional_continue = latest.close < prev.close
        pullback_depth_atr = _normalized(max(0.0, prev.high - ema_fast_now), atr_m15)
        reclaim_strength_atr = _normalized(ema_fast_now - latest.close, atr_m15)

    htf_aligned = h4_state in {"UP", "DOWN"} and h1_state == h4_state
    distance_ema20_atr = abs(latest.close - ema_fast_now) / atr_m15
    near_ema = distance_ema20_atr <= float(p.get("probe_distance_atr", 1.25))

    failed_original_gates: list[str] = []
    if not htf_aligned:
        failed_original_gates.append("REGIME_NOT_ALIGNED")
    if not pulled_back:
        failed_original_gates.append("PULLBACK_MISSING")
    if not reclaimed:
        failed_original_gates.append("RECLAIM_MISSING")

    if htf_aligned and pulled_back and reclaimed:
        tier = "A"
    elif thesis_source == "H4_LED" and (pulled_back or reclaimed) and directional_continue:
        tier = "B"
    elif near_ema and directional_continue:
        tier = "C"
    else:
        evidence = DecisionEvidence(
            ("EXPLORATION_TREND_NO_PROBE",),
            {
                "side": side,
                "h4": h4_state,
                "h1": h1_state,
                "thesis_source": thesis_source,
                "pulled_back": pulled_back,
                "reclaimed": reclaimed,
                "directional_continue": directional_continue,
                "distance_ema20_atr": distance_ema20_atr,
                "failed_original_gates": tuple(failed_original_gates),
            },
        )
        return StrategyResult(None, None, evidence, ("EXPLORATION_TREND_NO_PROBE",))

    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = float(p.get("volatility_buffer_atr", 0.25)) * atr_m15
    structure = min(bar.low for bar in m15[-5:]) if side == "BUY" else max(bar.high for bar in m15[-5:])
    stop = structure - buffer if side == "BUY" else structure + buffer
    risk = abs(entry - stop)
    if risk <= 0:
        evidence = DecisionEvidence(("INVALID_STOP_GEOMETRY",), {"entry": entry, "stop": stop})
        return StrategyResult(None, None, evidence, ("INVALID_STOP_GEOMETRY",))

    target_r = float(p.get("target_r", 2.0))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r
    evidence = DecisionEvidence(
        ("EXPLORATION_TREND_CANDIDATE",),
        {
            "family": "trend",
            "tier": tier,
            "side": side,
            "h4": h4_state,
            "h1": h1_state,
            "thesis_source": thesis_source,
            "htf_aligned": htf_aligned,
            "pulled_back": pulled_back,
            "reclaimed": reclaimed,
            "directional_continue": directional_continue,
            "failed_original_gates": tuple(failed_original_gates),
            "atr_m15": atr_m15,
            "ema20_slope_atr": _normalized(ema_fast_now - ema_fast_prev, atr_m15),
            "ema50_slope_atr": _normalized(ema_slow_now - ema_slow_prev, atr_m15),
            "ema_separation_atr": _normalized(ema_fast_now - ema_slow_now, atr_m15),
            "distance_ema20_atr": distance_ema20_atr,
            "pullback_depth_atr": pullback_depth_atr,
            "reclaim_strength_atr": reclaim_strength_atr,
            "candle_body_atr": _normalized(abs(latest.close - latest.open), atr_m15),
            "spread_atr": _normalized(snapshot.spread_cost, atr_m15),
            "structure": structure,
        },
    )
    expiry = now + timedelta(minutes=int(p.get("expiry_minutes", 45)))
    candidate = build_candidate(
        snapshot=snapshot,
        config=config,
        side=side,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        generated_at_utc=now,
        expires_at_utc=expiry,
        evidence=evidence,
    )
    return StrategyResult(candidate, Invalidation("STRUCTURE_BREAK", stop, "Exploration trend structure plus volatility buffer"), evidence)


def evaluate_breakout(snapshot: MarketSnapshot, config: StrategyConfig = BREAKOUT_CONFIG, now_utc: datetime | None = None) -> StrategyResult:
    now = now_utc or snapshot.captured_at_utc
    p = config.parameters
    tf = snapshot.timeframes.get("M15")
    range_bars = int(p.get("range_bars", 20))
    atr_period = int(p.get("atr_period", 14))
    if not tf or len(tf.closed_bars) < max(range_bars + 2, atr_period + 2, 51):
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prior = bars[-(range_bars + 1):-1]
    prior_high = max(bar.high for bar in prior)
    prior_low = min(bar.low for bar in prior)
    atr_prior = atr(bars[:-1], atr_period)
    if atr_prior <= 0:
        evidence = DecisionEvidence(("ATR_UNAVAILABLE",), {"atr": atr_prior})
        return StrategyResult(None, None, evidence, ("ATR_UNAVAILABLE",))

    side = "BUY" if latest.close > prior_high else "SELL" if latest.close < prior_low else None
    if side is None:
        evidence = DecisionEvidence(("NO_RANGE_BREAK",), {"prior_high": prior_high, "prior_low": prior_low, "close": latest.close})
        return StrategyResult(None, None, evidence, ("NO_RANGE_BREAK",))

    expansion = (latest.high - latest.low) / atr_prior
    trend_efficiency = efficiency(bars[:-1], 14)
    trend = trend_state(bars[:-1])
    wanted = "UP" if side == "BUY" else "DOWN"
    trend_ok = trend in {wanted, "MIXED"}
    efficiency_ok = trend_efficiency >= float(p.get("min_efficiency", 0.30))
    expansion_ok = expansion >= float(p.get("min_expansion", 1.2))
    boundary = prior_high if side == "BUY" else prior_low
    extension_atr = abs(latest.close - boundary) / atr_prior
    extension_ok = extension_atr <= float(p.get("max_extension_atr", 1.25))
    total_cost = snapshot.spread_cost + snapshot.commission_cost
    cost_atr = total_cost / atr_prior
    cost_ok = cost_atr <= float(p.get("max_cost_atr", 0.15))

    confirmations = sum((expansion_ok, efficiency_ok and trend_ok, extension_ok, cost_ok))
    failed_original_gates: list[str] = []
    if not expansion_ok:
        failed_original_gates.append("NO_VOLATILITY_EXPANSION")
    if not (efficiency_ok and trend_ok):
        failed_original_gates.append("TREND_STRENGTH_REJECT")
    if not extension_ok:
        failed_original_gates.append("OVEREXTENDED_BREAKOUT")
    if not cost_ok:
        failed_original_gates.append("COST_TOO_HIGH")

    tier = "A" if confirmations == 4 else "B" if confirmations >= 2 else "C"
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = float(p.get("stop_buffer_atr", 0.20)) * atr_prior
    stop = prior_low - buffer if side == "BUY" else prior_high + buffer
    risk = abs(entry - stop)
    if risk <= 0:
        evidence = DecisionEvidence(("INVALID_STOP_GEOMETRY",), {"entry": entry, "stop": stop})
        return StrategyResult(None, None, evidence, ("INVALID_STOP_GEOMETRY",))

    target_r = float(p.get("target_r", 2.0))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r
    evidence = DecisionEvidence(
        ("EXPLORATION_BREAKOUT_CANDIDATE",),
        {
            "family": "breakout",
            "tier": tier,
            "side": side,
            "failed_original_gates": tuple(failed_original_gates),
            "confirmations": confirmations,
            "prior_high": prior_high,
            "prior_low": prior_low,
            "atr": atr_prior,
            "expansion": expansion,
            "efficiency": trend_efficiency,
            "trend": trend,
            "trend_ok": trend_ok,
            "extension_atr": extension_atr,
            "cost_atr": cost_atr,
            "break_distance_atr": _normalized(abs(latest.close - boundary), atr_prior),
            "candle_body_atr": _normalized(abs(latest.close - latest.open), atr_prior),
        },
    )
    expiry = now + timedelta(minutes=int(p.get("expiry_minutes", 30)))
    candidate = build_candidate(
        snapshot=snapshot,
        config=config,
        side=side,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        generated_at_utc=now,
        expires_at_utc=expiry,
        evidence=evidence,
    )
    return StrategyResult(candidate, Invalidation("RANGE_REENTRY", stop, "Exploration breakout invalidation beyond prior range"), evidence)
