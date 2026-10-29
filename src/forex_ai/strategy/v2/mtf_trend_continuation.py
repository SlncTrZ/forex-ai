"""Multi-Timeframe Trend Continuation Strategy — H1/H4 EMA 50/200 + ADX Gate + M5 Value Area (EMA 20/50) Bounce.
Wing: code | Topic: mtf_trend_continuation | Updated: 2026-10-29 08:40
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.indicators import atr, dmi_adx, ema, sma, volume_zscore
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
    tf_name = str(p.get("decision_timeframe", "M5"))
    m5_tf = snapshot.timeframes.get(tf_name) or snapshot.timeframes.get("M5")
    h1_tf = snapshot.timeframes.get("H1")

    if not m5_tf or len(m5_tf.closed_bars) < 55:
        evidence = DecisionEvidence(("INSUFFICIENT_M5_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_M5_BARS",))

    if not h1_tf or len(h1_tf.closed_bars) < 55:
        evidence = DecisionEvidence(("INSUFFICIENT_H1_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_H1_BARS",))

    # 1. Session Gate: Tránh giờ Rollover (21:00 - 22:59 UTC)
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 7.0))
    s_end = float(p.get("session_end_hour", 17.0))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    # 2. H1 Trend & ADX Regime Gate (Cấm tiệt khi thị trường đi ngang / chop)
    h1_bars = h1_tf.closed_bars
    h1_closes = [b.close for b in h1_bars]
    h1_ema20 = ema(h1_closes, 20)
    h1_ema50 = ema(h1_closes, 50)

    adx_obj = dmi_adx(h1_bars, 14)
    min_adx = float(p.get("min_h1_adx", 20.0))
    if not adx_obj or adx_obj.adx < min_adx:
        evidence = DecisionEvidence(("H1_LOW_ADX_CHOP",), {"adx": adx_obj.adx if adx_obj else 0.0})
        return StrategyResult(None, None, evidence, ("H1_LOW_ADX_CHOP",))

    h1_trend = "UP" if h1_ema20 > h1_ema50 else ("DOWN" if h1_ema20 < h1_ema50 else "MIXED")
    if h1_trend == "MIXED":
        evidence = DecisionEvidence(("H1_TREND_MIXED",), {})
        return StrategyResult(None, None, evidence, ("H1_TREND_MIXED",))

    # 3. M5 Value Area Pullback (Vùng giữa EMA 20 và EMA 50 trên M5)
    m5_bars = m5_tf.closed_bars
    latest = m5_bars[-1]
    prev = m5_bars[-2]
    m5_closes = [b.close for b in m5_bars]
    m5_ema20 = ema(m5_closes, 20)
    m5_ema50 = ema(m5_closes, 50)
    m5_atr = atr(m5_bars[:-1], 14)

    if m5_atr <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    side: str | None = None
    stop_ref_price: float = 0.0

    # Kịch bản BUY: H1 Uptrend, M5 giá vừa hồi chạm vào Value Area (giữa EMA20 và EMA50) rồi nến này bật tăng xanh
    if h1_trend == "UP" and m5_ema20 > m5_ema50:
        in_value_area = prev.low <= m5_ema20 and prev.low >= m5_ema50 - 0.2 * m5_atr
        bounced_up = latest.close > m5_ema20 and latest.close > latest.open
        if in_value_area and bounced_up:
            side = "BUY"
            stop_ref_price = m5_ema50

    # Kịch bản SELL: H1 Downtrend, M5 giá vừa hồi chạm vào Value Area rồi nến này sập đỏ xuống dưới EMA20
    elif h1_trend == "DOWN" and m5_ema20 < m5_ema50:
        in_value_area = prev.high >= m5_ema20 and prev.high <= m5_ema50 + 0.2 * m5_atr
        bounced_down = latest.close < m5_ema20 and latest.close < latest.open
        if in_value_area and bounced_down:
            side = "SELL"
            stop_ref_price = m5_ema50

    if not side:
        evidence = DecisionEvidence(("NO_VALUE_AREA_SETUP",), {"h1_trend": h1_trend, "m5_ema20": m5_ema20, "m5_ema50": m5_ema50})
        return StrategyResult(None, None, evidence, ("NO_VALUE_AREA_SETUP",))

    # 4. Volume Z-Score Confirmation (Xung lực nổ volume)
    vol_z = volume_zscore(m5_bars, 20)
    min_vol_z = float(p.get("min_volume_zscore", 0.8))
    if vol_z is not None and vol_z < min_vol_z:
        evidence = DecisionEvidence(("LOW_VOLUME_MOMENTUM",), {"vol_z": vol_z, "min_required": min_vol_z})
        return StrategyResult(None, None, evidence, ("LOW_VOLUME_MOMENTUM",))

    # 5. Entry & Stop Loss & Target:
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = 0.2 * m5_atr
    min_stop_dist = float(p.get("min_stop_distance", 5.0 if "XAU" in snapshot.symbol.upper() else 0.0015))

    if side == "BUY":
        raw_stop = stop_ref_price - buffer
        raw_dist = abs(entry - raw_stop)
        actual_dist = max(raw_dist, min_stop_dist)
        stop = entry - actual_dist
    else:
        raw_stop = stop_ref_price + buffer
        raw_dist = abs(raw_stop - entry)
        actual_dist = max(raw_dist, min_stop_dist)
        stop = entry + actual_dist

    target_r = float(p.get("target_r", 1.8))
    target = entry + actual_dist * target_r if side == "BUY" else entry - actual_dist * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 60)))
    evidence = DecisionEvidence(
        ("MTF_TREND_CONTINUATION_CONFIRMED",),
        {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "h1_trend": h1_trend,
            "vol_z": vol_z,
            "risk_dist": actual_dist,
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
        invalidation=Invalidation("VALUE_AREA_BREAK_FAIL", stop, "Giá phá thủng đường EMA 50 của Value Area"),
        evidence=evidence,
    )
