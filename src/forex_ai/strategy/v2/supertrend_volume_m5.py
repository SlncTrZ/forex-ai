"""Supertrend + Volume M5 Strategy — Trend following with volume confirmation on M5 timeframe.
Wing: code | Topic: supertrend_m5 | Updated: 2026-10-28 13:30
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.indicators import atr, sma, supertrend, volume_zscore
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

    # 1. Bộ lọc Phiên: Tránh giờ Rollover (21:00 - 22:59 UTC)
    t = now_utc.astimezone(UTC)
    if t.hour in {21, 22}:
        evidence = DecisionEvidence(("ROLLOVER_HOUR_BLACKOUT",), {"hour": t.hour})
        return StrategyResult(None, None, evidence, ("ROLLOVER_HOUR_BLACKOUT",))

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

    # 2. Bộ lọc Khối lượng (Volume Filter):
    # Khối lượng nến hiện tại phải >= min_vol_ratio * SMA(volume, 20)
    volumes = [float(b.volume) for b in bars]
    vol_sma = sma(volumes[:-1], 20)
    min_vol_ratio = float(p.get("min_volume_ratio", 1.2))
    vol_ratio = latest.volume / max(vol_sma, 1.0)

    if vol_ratio < min_vol_ratio:
        evidence = DecisionEvidence(("VOLUME_BELOW_THRESHOLD",), {"vol_ratio": vol_ratio, "min_required": min_vol_ratio})
        return StrategyResult(None, None, evidence, ("VOLUME_BELOW_THRESHOLD",))

    # 3. Kịch bản vào lệnh:
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

    # 4. Stop Loss & Take Profit:
    # Stop Loss đặt ngay tại đường Supertrend (có đệm 0.1 ATR)
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = 0.1 * atr_val

    if side == "BUY":
        stop = st.value - buffer
    else:
        stop = st.value + buffer

    risk = abs(entry - stop)
    # Ràng buộc Risk tối thiểu 0.3 ATR, tối đa 2.5 ATR
    if risk < 0.3 * atr_val or risk > 2.5 * atr_val:
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
    )
    return StrategyResult(
        candidate=candidate,
        invalidation=Invalidation("SUPERTREND_FLIP_FAIL", stop, "Giá phá thủng đường Supertrend"),
        evidence=evidence,
    )
