"""Supertrend + Stochastic M5 Strategy — Trend following with oscillator entry timing.
Wing: code | Topic: supertrend_stochastic_m5 | Updated: 2026-10-30 20:37
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.indicators import atr, ema, stochastic, supertrend
from forex_ai.strategy.v1.contracts import (
    CandidateEnvelope,
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
    tf_name = str(p.get("decision_timeframe", "M5" if "M5" in snapshot.timeframes else "M15"))
    tf = snapshot.timeframes.get(tf_name)
    if not tf or len(tf.closed_bars) < 30:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]

    # 1. Bộ lọc Phiên
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 0.0))
    s_end = float(p.get("session_end_hour", 24.0))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour, "start": s_start, "end": s_end})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    # 1b. Bộ lọc Xu hướng H1 (Tùy chọn)
    if bool(p.get("require_h1_trend", False)):
        h1_tf = snapshot.timeframes.get("H1")
        if h1_tf and len(h1_tf.closed_bars) >= 50:
            h1_closes = [b.close for b in h1_tf.closed_bars]
            h1_fast = ema(h1_closes, 50)
            h1_slow = ema(h1_closes, 200)
            h1_trend = "UP" if h1_fast > h1_slow else "DOWN"
        else:
            h1_trend = "UNKNOWN"
    else:
        h1_trend = "IGNORED"

    # 2. Supertrend (Mặc định: period = 10, multiplier = 3.0)
    st_period = int(p.get("supertrend_period", 10))
    st_mult = float(p.get("supertrend_multiplier", 3.0))
    st = supertrend(bars, period=st_period, multiplier=st_mult)
    if not st:
        evidence = DecisionEvidence(("SUPERTREND_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("SUPERTREND_UNAVAILABLE",))

    # 3. Stochastic Oscillator (%K=7, %K smooth=1, %D smooth=3)
    k_len = int(p.get("stoch_k_period", 7))
    d_len = int(p.get("stoch_d_period", 3))
    stoch_curr = stochastic(bars, k_len, d_len)
    stoch_prev = stochastic(bars[:-1], k_len, d_len)
    if not stoch_curr or not stoch_prev:
        evidence = DecisionEvidence(("STOCHASTIC_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("STOCHASTIC_UNAVAILABLE",))

    oversold = float(p.get("stoch_oversold", 20.0))
    overbought = float(p.get("stoch_overbought", 80.0))
    trigger_mode = str(p.get("trigger_mode", "K_CROSS_D"))

    side: str | None = None
    trigger_type = "NONE"

    # Điều kiện BUY: Supertrend UP + Stochastic chạm hoặc cắt xuống 20 (hoặc turn-around nếu cấu hình)
    if st.trend == "UP" and latest.close > st.value:
        if trigger_mode == "CROSS_DOWN":
            if stoch_curr.percent_k <= oversold and stoch_prev.percent_k > oversold:
                side = "BUY"
                trigger_type = "STOCH_CROSS_DOWN_20"
            elif bool(p.get("allow_touch", False)) and stoch_curr.percent_k <= oversold:
                side = "BUY"
                trigger_type = "STOCH_TOUCH_20"
        elif trigger_mode == "CROSS_UP":
            if stoch_curr.percent_k >= oversold and stoch_prev.percent_k < oversold:
                side = "BUY"
                trigger_type = "STOCH_CROSS_UP_20"
        elif trigger_mode == "K_CROSS_D":
            if stoch_curr.percent_k > stoch_curr.percent_d and stoch_prev.percent_k <= stoch_prev.percent_d and stoch_curr.percent_k <= (oversold + 10.0):
                side = "BUY"
                trigger_type = "STOCH_GOLDEN_CROSS"
        elif trigger_mode == "PINBAR":
            lower_wick = min(latest.open, latest.close) - latest.low
            body = abs(latest.close - latest.open)
            if lower_wick >= 1.5 * body and latest.close > latest.open and stoch_curr.percent_k <= (oversold + 10.0):
                side = "BUY"
                trigger_type = "PINBAR_PULLBACK"

    # Điều kiện SELL: Supertrend DOWN + Stochastic chạm hoặc cắt lên 80 (hoặc turn-around nếu cấu hình)
    elif st.trend == "DOWN" and latest.close < st.value:
        if trigger_mode == "CROSS_DOWN":
            if stoch_curr.percent_k >= overbought and stoch_prev.percent_k < overbought:
                side = "SELL"
                trigger_type = "STOCH_CROSS_UP_80"
            elif bool(p.get("allow_touch", False)) and stoch_curr.percent_k >= overbought:
                side = "SELL"
                trigger_type = "STOCH_TOUCH_80"
        elif trigger_mode == "CROSS_UP":
            if stoch_curr.percent_k <= overbought and stoch_prev.percent_k > overbought:
                side = "SELL"
                trigger_type = "STOCH_CROSS_DOWN_80"
        elif trigger_mode == "K_CROSS_D":
            if stoch_curr.percent_k < stoch_curr.percent_d and stoch_prev.percent_k >= stoch_prev.percent_d and stoch_curr.percent_k >= (overbought - 10.0):
                side = "SELL"
                trigger_type = "STOCH_DEATH_CROSS"
        elif trigger_mode == "PINBAR":
            upper_wick = latest.high - max(latest.open, latest.close)
            body = abs(latest.close - latest.open)
            if upper_wick >= 1.5 * body and latest.close < latest.open and stoch_curr.percent_k >= (overbought - 10.0):
                side = "SELL"
                trigger_type = "PINBAR_PULLBACK"

    if side is None:
        evidence = DecisionEvidence(
            ("NO_SETUP",),
            {
                "trend": st.trend,
                "st_value": st.value,
                "k": stoch_curr.percent_k,
                "d": stoch_curr.percent_d,
                "close": latest.close,
            },
        )
        return StrategyResult(None, None, evidence, ("NO_SETUP",))

    # Kiểm tra H1 Trend Alignment (nếu bật)
    if h1_trend in {"UP", "DOWN"}:
        if side == "BUY" and h1_trend != "UP":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1_trend": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))
        if side == "SELL" and h1_trend != "DOWN":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1_trend": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))

    # 4. Stop Loss & Take Profit:
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    sl_buffer = float(p.get("sl_buffer", 3.0))
    sl_mode = str(p.get("sl_mode", "ST_BUFFER"))

    if sl_mode == "ATR":
        atr_val = atr(bars[:-1], 14)
        if atr_val > 0:
            stop = entry - sl_buffer * atr_val if side == "BUY" else entry + sl_buffer * atr_val
        else:
            stop = st.value - sl_buffer if side == "BUY" else st.value + sl_buffer
    else:
        # Stop Loss nằm dưới/trên Supertrend sl_buffer ($3.0 giá cho Vàng)
        stop = st.value - sl_buffer if side == "BUY" else st.value + sl_buffer

    risk = abs(entry - stop)
    max_sl_dist = float(p.get("max_sl_distance", 15.0))
    if risk < 1.0 or risk > max_sl_dist:
        evidence = DecisionEvidence(("INVALID_RISK_RANGE",), {"risk": risk, "max_allowed": max_sl_dist})
        return StrategyResult(None, None, evidence, ("INVALID_RISK_RANGE",))

    target_r = float(p.get("target_r", 1.5))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 60)))
    evidence = DecisionEvidence(
        ("SUPERTREND_STOCHASTIC_CONFIRMED",),
        {
            "side": side,
            "trigger": trigger_type,
            "entry": entry,
            "stop": stop,
            "target": target,
            "st_value": st.value,
            "k": stoch_curr.percent_k,
            "d": stoch_curr.percent_d,
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
        invalidation=Invalidation("SUPERTREND_STOCH_FAIL", stop, "Giá phá vỡ ngưỡng Stop Loss Supertrend"),
        evidence=evidence,
    )
