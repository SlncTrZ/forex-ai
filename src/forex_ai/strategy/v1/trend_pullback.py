from __future__ import annotations

from datetime import datetime, timedelta

from .contracts import DecisionEvidence, Invalidation, MarketSnapshot, StrategyConfig, StrategyResult, StrategyVersion, build_candidate
from .indicators import atr, ema, trend_state

DEFAULT_CONFIG = StrategyConfig(
    StrategyVersion("trend_pullback_v1", "1.0.0"),
    {
        "ema_fast": 20,
        "ema_slow": 50,
        "pullback_atr": 0.75,
        "volatility_buffer_atr": 0.25,
        "target_r": 2.0,
        "expiry_minutes": 45,
        "m5_confirm": False,
    },
)


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
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
    if h4_state not in {"UP", "DOWN"} or h1_state != h4_state:
        evidence = DecisionEvidence(("REGIME_NOT_ALIGNED",), {"h4": h4_state, "h1": h1_state})
        return StrategyResult(None, None, evidence, ("REGIME_NOT_ALIGNED",))

    side = "BUY" if h4_state == "UP" else "SELL"
    closes = [bar.close for bar in m15]
    ema_fast = ema(closes, fast)
    atr_m15 = atr(m15, 14)
    prev, latest = m15[-2], m15[-1]
    allowance = float(p.get("pullback_atr", 0.75)) * atr_m15
    if side == "BUY":
        pulled_back = prev.low <= ema_fast + allowance
        reclaimed = latest.close > ema_fast and latest.close > prev.close
    else:
        pulled_back = prev.high >= ema_fast - allowance
        reclaimed = latest.close < ema_fast and latest.close < prev.close

    if not (pulled_back and reclaimed):
        evidence = DecisionEvidence(
            ("NO_PULLBACK_RECLAIM",),
            {"side": side, "ema_fast": ema_fast, "atr": atr_m15, "pulled_back": pulled_back, "reclaimed": reclaimed},
        )
        return StrategyResult(None, None, evidence, ("NO_PULLBACK_RECLAIM",))

    if bool(p.get("m5_confirm", False)):
        m5 = snapshot.timeframes.get("M5")
        if not m5 or trend_state(m5.closed_bars, fast, slow) != h4_state:
            evidence = DecisionEvidence(("M5_NOT_CONFIRMED",), {"side": side})
            return StrategyResult(None, None, evidence, ("M5_NOT_CONFIRMED",))

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
        ("TREND_PULLBACK_CONFIRMED",),
        {"side": side, "h4": h4_state, "h1": h1_state, "ema_m15": ema_fast, "atr_m15": atr_m15, "structure": structure},
    )
    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 45)))
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
    )
    return StrategyResult(candidate, Invalidation("STRUCTURE_BREAK", stop, "M15 structure plus volatility buffer"), evidence)
