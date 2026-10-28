"""Supertrend + Volume M5 Strategy — Trend following with volume confirmation on M5 timeframe.
Wing: code | Topic: supertrend_m5 | Updated: 2026-10-28 13:30
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.indicators import atr, ema, sma, supertrend, volume_zscore
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
    # Ưu tiên lấy M5, nếu snapshot chỉ có M15 thì dùng M15
    tf = snapshot.timeframes.get("M5") or snapshot.timeframes.get("M15")
    if not tf or len(tf.closed_bars) < 30:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prev = bars[-2]

    tf_name = "M5" if "M5" in snapshot.timeframes else "M15"
    tf = snapshot.timeframes.get(tf_name)
    if not tf or len(tf.closed_bars) < 30:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prev = bars[-2]

    # 1. Bộ lọc Phiên: Tránh giờ Rollover (21:00 - 22:59 UTC) và chỉ trade phiên hoạt động (London + NY)
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 7.5))
    s_end = float(p.get("session_end_hour", 16.5))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour, "start": s_start, "end": s_end})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    # 1b. Bộ lọc Xu hướng lớn H1 (H1 Trend Bias Alignment)
    if bool(p.get("require_h1_trend", True)):
        h1_tf = snapshot.timeframes.get("H1")
        if h1_tf and len(h1_tf.closed_bars) >= 50:
            h1_closes = [b.close for b in h1_tf.closed_bars]
            h1_fast = ema(h1_closes, 20)
            h1_slow = ema(h1_closes, 50)
            h1_trend = "UP" if h1_fast > h1_slow else "DOWN"
        else:
            h1_trend = "UNKNOWN"
    else:
        h1_trend = "IGNORED"

    st_period = int(p.get("supertrend_period", 10))
    st_mult = float(p.get("supertrend_multiplier", 3.0))

    st = supertrend(bars, period=st_period, multiplier=st_mult)
    if not st:
        evidence = DecisionEvidence(("SUPERTREND_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("SUPERTREND_UNAVAILABLE",))

    atr_val = atr(bars[:-1], 14)
    if atr_val <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    # 2. Kịch bản vào lệnh:
    # A. Trend Flip (Đảo chiều xu hướng vừa xuất hiện trên nến này)
    # B. Pullback Bounce (Trong xu hướng rõ ràng, giá hồi về test đường Supertrend rồi bật tăng)
    side: str | None = None
    trigger_type = "NONE"

    allow_pullback = bool(p.get("allow_pullback_entry", True))

    if st.changed:
        # Trend vừa đổi chiều
        if st.trend == "UP" and latest.close > latest.open:
            side = "BUY"
            trigger_type = "TREND_FLIP"
        elif st.trend == "DOWN" and latest.close < latest.open:
            side = "SELL"
            trigger_type = "TREND_FLIP"
    elif allow_pullback:
        # Đang trong trend, nến trước chạm sát Supertrend và nến này bật ra
        dist_to_st = abs(latest.close - st.value) / atr_val
        if dist_to_st <= 1.2:
            if st.trend == "UP" and latest.close > latest.open and latest.low <= st.value + 0.3 * atr_val:
                side = "BUY"
                trigger_type = "PULLBACK_BOUNCE"
            elif st.trend == "DOWN" and latest.close < latest.open and latest.high >= st.value - 0.3 * atr_val:
                side = "SELL"
                trigger_type = "PULLBACK_BOUNCE"

    if side is None:
        evidence = DecisionEvidence(("NO_SUPERTREND_SETUP",), {"trend": st.trend, "st_value": st.value, "close": latest.close})
        return StrategyResult(None, None, evidence, ("NO_SUPERTREND_SETUP",))

    # 3. Bộ lọc Khối lượng (Volume Filter) cho Setup đã xuất hiện:
    volumes = [float(b.volume) for b in bars]
    vol_sma = sma(volumes[:-1], 20)
    vol_ratio = latest.volume / max(vol_sma, 1.0)
    min_vol_ratio = float(p.get("min_volume_ratio", 1.05)) if trigger_type == "TREND_FLIP" else float(p.get("min_pullback_volume_ratio", 0.95))

    if vol_ratio < min_vol_ratio:
        evidence = DecisionEvidence(("VOLUME_BELOW_THRESHOLD",), {"vol_ratio": vol_ratio, "min_required": min_vol_ratio, "trigger": trigger_type})
        return StrategyResult(None, None, evidence, ("VOLUME_BELOW_THRESHOLD",))

    # Kiểm tra H1 Trend Alignment
    if h1_trend in {"UP", "DOWN"}:
        if side == "BUY" and h1_trend != "UP":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1_trend": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))
        if side == "SELL" and h1_trend != "DOWN":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1_trend": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))

    # 4. Stop Loss & Take Profit:
    # Stop Loss đặt ngay tại đường Supertrend có buffer, tối thiểu min_stop_distance (500 pts cho Vàng)
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = 0.1 * atr_val

    if side == "BUY":
        raw_stop = st.value - buffer
    else:
        raw_stop = st.value + buffer

    raw_risk = abs(entry - raw_stop)
    min_stop_dist = float(p.get("min_stop_distance", 0.0))
    if min_stop_dist <= 0.0 and ("XAU" in snapshot.symbol.upper() or "GOLD" in snapshot.symbol.upper()):
        min_stop_dist = 5.0  # Mặc định an toàn tối thiểu 5 giá ($5.0 = 500 points) cho Vàng

    risk = max(raw_risk, min_stop_dist)
    if side == "BUY":
        stop = entry - risk
    else:
        stop = entry + risk

    # Ràng buộc Risk tối thiểu 0.2 ATR, tối đa 8.0 ATR
    if risk < 0.2 * atr_val or risk > 8.0 * atr_val:
        evidence = DecisionEvidence(("INVALID_RISK_RANGE",), {"risk": risk, "atr": atr_val})
        return StrategyResult(None, None, evidence, ("INVALID_RISK_RANGE",))

    target_r = float(p.get("target_r", 1.8))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 60)))
    evidence = DecisionEvidence(
        ("SUPERTREND_VOLUME_CONFIRMED",),
        {
            "side": side,
            "trigger": trigger_type,
            "entry": entry,
            "stop": stop,
            "target": target,
            "st_value": st.value,
            "vol_ratio": vol_ratio,
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
        invalidation=Invalidation("SUPERTREND_FLIP_FAIL", stop, "Giá phá thủng đường Supertrend"),
        evidence=evidence,
    )
