"""Supertrend Pinbar M5 Strategy — Supertrend trend following with Pinbar price rejection.
Wing: code | Topic: supertrend_pinbar_m5 | Updated: 2026-10-30 20:45
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.market.indicators import atr, ema, supertrend
from forex_ai.strategy.v1.contracts import (
    DecisionEvidence,
    Invalidation,
    MarketSnapshot,
    StrategyConfig,
    StrategyResult,
    build_candidate,
)

UTC = timezone.utc


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
    p = config.parameters
    tf_name = "M5" if "M5" in snapshot.timeframes else "M15"
    tf = snapshot.timeframes.get(tf_name)
    if not tf or len(tf.closed_bars) < 30:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]

    # 1. Bộ lọc Phiên
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 6.0))
    s_end = float(p.get("session_end_hour", 15.5))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour, "start": s_start, "end": s_end})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    # 2. H1 Trend Bias
    if bool(p.get("require_h1_trend", True)):
        h1_tf = snapshot.timeframes.get("H1")
        if h1_tf and len(h1_tf.closed_bars) >= 50:
            h1_closes = [b.close for b in h1_tf.closed_bars]
            h1_fast = ema(h1_closes, 21)
            h1_slow = ema(h1_closes, 50)
            h1_trend = "UP" if h1_fast > h1_slow else "DOWN"
        else:
            h1_trend = "UNKNOWN"
    else:
        h1_trend = "IGNORED"

    # 3. Supertrend & ATR
    st_period = int(p.get("supertrend_period", 10))
    st_mult = float(p.get("supertrend_multiplier", 3.0))
    st = supertrend(bars, period=st_period, multiplier=st_mult)
    atr_val = atr(bars[:-1], 14)
    if not st or atr_val <= 0:
        evidence = DecisionEvidence(("INDICATOR_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("INDICATOR_UNAVAILABLE",))

    # Khoảng cách tới Supertrend
    dist_to_st = abs(latest.close - st.value)
    if dist_to_st > 1.5 * atr_val:
        evidence = DecisionEvidence(("FAR_FROM_SUPERTREND",), {"dist": dist_to_st, "max": 1.5 * atr_val})
        return StrategyResult(None, None, evidence, ("FAR_FROM_SUPERTREND",))

    # 4. Nhận diện nến Pinbar
    body = abs(latest.close - latest.open)
    lower_wick = min(latest.open, latest.close) - latest.low
    upper_wick = latest.high - max(latest.open, latest.close)

    side: str | None = None
    trigger_type = "NONE"

    # BUY: Supertrend UP + Pinbar rút chân tăng
    if st.trend == "UP" and latest.close > st.value:
        if lower_wick >= 1.5 * body and latest.close > latest.open:
            side = "BUY"
            trigger_type = "BULLISH_PINBAR_BOUNCE"

    # SELL: Supertrend DOWN + Shooting Star rút râu trên
    elif st.trend == "DOWN" and latest.close < st.value:
        if upper_wick >= 1.5 * body and latest.close < latest.open:
            side = "SELL"
            trigger_type = "BEARISH_PINBAR_REJECTION"

    if side is None:
        evidence = DecisionEvidence(("NO_SETUP",), {"trend": st.trend, "body": body, "lower_wick": lower_wick, "upper_wick": upper_wick})
        return StrategyResult(None, None, evidence, ("NO_SETUP",))

    if h1_trend in {"UP", "DOWN"}:
        if side == "BUY" and h1_trend != "UP":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))
        if side == "SELL" and h1_trend != "DOWN":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))

    # 5. Stop Loss & Take Profit
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    sl_atr_mult = float(p.get("sl_atr_multiplier", 2.0))
    stop = entry - sl_atr_mult * atr_val if side == "BUY" else entry + sl_atr_mult * atr_val
    risk = abs(entry - stop)

    max_sl = float(p.get("max_sl_distance", 6.5))
    if risk < 0.8 or risk > max_sl:
        evidence = DecisionEvidence(("INVALID_RISK_RANGE",), {"risk": risk, "max_allowed": max_sl})
        return StrategyResult(None, None, evidence, ("INVALID_RISK_RANGE",))

    target_r = float(p.get("target_r", 1.6))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 60)))
    evidence = DecisionEvidence(
        ("SUPERTREND_PINBAR_CONFIRMED",),
        {
            "side": side,
            "trigger": trigger_type,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk": risk,
            "target_r": target_r,
        },
    )

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
        decision_timeframe=tf_name,
    )
    return StrategyResult(
        candidate=candidate,
        invalidation=Invalidation("PINBAR_FAIL", stop, "Giá phá vỡ đáy/đỉnh nến Pinbar"),
        evidence=evidence,
    )
