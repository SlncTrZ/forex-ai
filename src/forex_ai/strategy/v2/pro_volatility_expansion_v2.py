"""Pro Volatility Expansion V2 Strategy — Carter Squeeze, SMC Displacement, FVG, Volume Z-Score, and Tight Structural SL.
Wing: code | Topic: pro_volatility_v2 | Updated: 2026-10-28 13:28
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.candles import candle_shape
from forex_ai.market.indicators import atr, bollinger_squeeze, volume_zscore
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
    tf = snapshot.timeframes.get("M15") or snapshot.timeframes.get("M5")
    if not tf or len(tf.closed_bars) < 30:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prev = bars[-2]

    # 1. Bộ lọc Phiên giao dịch (Session Filter): Tránh giờ Rollover (21:00 - 22:59 UTC)
    t = now_utc.astimezone(UTC)
    if t.hour in {21, 22}:
        evidence = DecisionEvidence(("ROLLOVER_HOUR_BLACKOUT",), {"hour": t.hour})
        return StrategyResult(None, None, evidence, ("ROLLOVER_HOUR_BLACKOUT",))

    atr_period = int(p.get("atr_period", 14))
    atr_val = atr(bars[:-1], atr_period)
    if atr_val <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    # 2. Pha Nén (Squeeze Phase): Đo Carter Squeeze hoặc co thắt biên độ trong 20 nến trước
    squeeze_state = bollinger_squeeze(bars[:-1], bb_period=20, bb_mult=2.0, kc_mult=1.5)
    # Nếu không bật Squeeze thì kiểm tra xem dải bandwidth có đang thấp không
    recent_ranges = [b.high - b.low for b in bars[-10:-1]]
    avg_recent_range = sum(recent_ranges) / len(recent_ranges)
    was_compressed = (squeeze_state and squeeze_state.squeeze_on) or (avg_recent_range < 1.0 * atr_val)

    if not was_compressed:
        evidence = DecisionEvidence(("NO_PRIOR_COMPRESSION",), {"avg_recent_range": avg_recent_range, "atr": atr_val})
        return StrategyResult(None, None, evidence, ("NO_PRIOR_COMPRESSION",))

    # 3. Pha Bùng nổ (Displacement & Shape): Nến bứt phá có thân nến lớn, biên độ rộng
    shape = candle_shape(latest, atr_val)
    min_body_ratio = float(p.get("min_body_ratio", 0.65))
    min_range_atr = float(p.get("min_range_atr", 1.2))

    if shape.body_ratio < min_body_ratio or shape.k_range < min_range_atr:
        evidence = DecisionEvidence(("WEAK_EXPANSION_CANDLE",), {"body_ratio": shape.body_ratio, "k_range": shape.k_range})
        return StrategyResult(None, None, evidence, ("WEAK_EXPANSION_CANDLE",))

    # 4. Xác nhận Khối lượng (Volume Z-Score): Khối lượng dòng lệnh bùng nổ
    vol_z = volume_zscore(bars, 20)
    min_vol_z = float(p.get("min_volume_zscore", 1.2))
    if vol_z is None or vol_z < min_vol_z:
        evidence = DecisionEvidence(("INSUFFICIENT_VOLUME_MOMENTUM",), {"volume_zscore": vol_z})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_VOLUME_MOMENTUM",))

    # 5. Xác định hướng Breakout
    prior_high = max(b.high for b in bars[-15:-1])
    prior_low = min(b.low for b in bars[-15:-1])

    side: str | None = None
    if shape.direction == "BULL" and latest.close > prior_high:
        side = "BUY"
    elif shape.direction == "BEAR" and latest.close < prior_low:
        side = "SELL"

    if side is None:
        evidence = DecisionEvidence(("NO_CLEAR_BREAKOUT_DIRECTION",), {"close": latest.close, "prior_high": prior_high, "prior_low": prior_low})
        return StrategyResult(None, None, evidence, ("NO_CLEAR_BREAKOUT_DIRECTION",))

    # 6. Cắt lỗ Chặt chẽ (Tight Structural Stop Loss):
    # Đặt ngay sau đáy nến bứt phá (đối với BUY) hoặc sau đỉnh nến bứt phá (đối với SELL)
    # kèm theo một buffer nhỏ (0.2 ATR), thay vì gồng lỗ 20 nến trước!
    buffer = 0.2 * atr_val
    entry = snapshot.ask if side == "BUY" else snapshot.bid

    if side == "BUY":
        stop = latest.low - buffer
    else:
        stop = latest.high + buffer

    risk = abs(entry - stop)
    # Giới hạn rủi ro hợp lý: risk tối thiểu 0.5 ATR, tối đa 2.0 ATR
    if risk < 0.4 * atr_val or risk > 2.5 * atr_val:
        evidence = DecisionEvidence(("INVALID_RISK_ENVELOPE",), {"risk": risk, "atr": atr_val})
        return StrategyResult(None, None, evidence, ("INVALID_RISK_ENVELOPE",))

    target_r = float(p.get("target_r", 2.0))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 90)))
    evidence = DecisionEvidence(
        ("PRO_VOLATILITY_EXPANSION_CONFIRMED",),
        {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk": risk,
            "target_r": target_r,
            "body_ratio": shape.body_ratio,
            "k_range": shape.k_range,
            "volume_z": vol_z,
            "atr": atr_val,
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
        invalidation=Invalidation("FALSE_BREAKOUT_FAIL", stop, "Phá vỡ giả - giá chui ngược lại chân nến bứt phá"),
        evidence=evidence,
    )
