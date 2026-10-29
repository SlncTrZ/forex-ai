"""SMC Pro FVG Strategy — Liquidity Sweep + Market Structure Shift (MSS) + FVG 50% CE Entry.
Wing: code | Topic: smc_pro_fvg | Updated: 2026-10-29 08:30
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.candles import candle_shape
from forex_ai.market.indicators import atr
from forex_ai.market.smc import detect_fair_value_gaps, detect_swing_points
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
    tf = snapshot.timeframes.get(tf_name) or snapshot.timeframes.get("M5") or snapshot.timeframes.get("M15")
    if not tf or len(tf.closed_bars) < 35:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]

    # 1. Session Gate: Tránh giờ Rollover (21:00 - 22:59 UTC)
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 7.0))
    s_end = float(p.get("session_end_hour", 17.0))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    atr_val = atr(bars[:-1], 14)
    if atr_val <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    # 2. Tìm các điểm Swing Points (Đỉnh / Đáy thanh khoản gần nhất)
    swings = detect_swing_points(bars[:-2], left=3, right=3)
    if not swings:
        evidence = DecisionEvidence(("NO_SWING_POINTS",), {})
        return StrategyResult(None, None, evidence, ("NO_SWING_POINTS",))

    swing_highs = [s for s in swings if s.kind == "HIGH"]
    swing_lows = [s for s in swings if s.kind == "LOW"]
    if not swing_highs or not swing_lows:
        evidence = DecisionEvidence(("INSUFFICIENT_SWINGS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_SWINGS",))

    recent_sh = swing_highs[-1]
    recent_sl = swing_lows[-1]

    # 3. Tìm Fair Value Gap (FVG) hợp lệ trong 5 nến gần nhất
    atrs_list = [atr(bars[: k + 1], 14) for k in range(len(bars))]
    fvgs = detect_fair_value_gaps(bars[-8:], atrs_list[-8:], min_gap_atr=0.15)

    side: str | None = None
    fvg_target_ce: float = 0.0
    sweep_ref_price: float = 0.0

    # Kịch bản BULLISH:
    # Giá vừa quét đáy Swing Low (Liquidity Sweep) và nến gần nhất bật tăng tạo Bullish FVG
    bullish_fvgs = [f for f in fvgs if f.side == "BULLISH" and not f.mitigated]
    if bullish_fvgs and any(b.low < recent_sl.price for b in bars[-4:]):
        # Xác nhận có sweep đáy
        fvg = bullish_fvgs[-1]
        # Giá hiện tại đang hồi về vùng FVG (chạm hoặc gần mức 50% CE)
        if latest.low <= fvg.top and latest.close >= fvg.consequent_encroachment:
            side = "BUY"
            fvg_target_ce = fvg.consequent_encroachment
            sweep_ref_price = min(b.low for b in bars[-4:])

    # Kịch bản BEARISH:
    # Giá vừa quét đỉnh Swing High và nến gần nhất sập mạnh tạo Bearish FVG
    bearish_fvgs = [f for f in fvgs if f.side == "BEARISH" and not f.mitigated]
    if bearish_fvgs and any(b.high > recent_sh.price for b in bars[-4:]):
        fvg = bearish_fvgs[-1]
        if latest.high >= fvg.bottom and latest.close <= fvg.consequent_encroachment:
            side = "SELL"
            fvg_target_ce = fvg.consequent_encroachment
            sweep_ref_price = max(b.high for b in bars[-4:])

    if not side:
        evidence = DecisionEvidence(("NO_SMC_SETUP",), {"recent_sh": recent_sh.price, "recent_sl": recent_sl.price})
        return StrategyResult(None, None, evidence, ("NO_SMC_SETUP",))

    # 4. Entry & Stop Loss & Target:
    # Entry tại giá hiện tại (hoặc limit tại FVG CE)
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = 0.2 * atr_val
    min_stop_dist = float(p.get("min_stop_distance", 5.0 if "XAU" in snapshot.symbol.upper() else 0.0015))

    if side == "BUY":
        raw_stop = sweep_ref_price - buffer
        raw_dist = abs(entry - raw_stop)
        actual_dist = max(raw_dist, min_stop_dist)
        stop = entry - actual_dist
    else:
        raw_stop = sweep_ref_price + buffer
        raw_dist = abs(raw_stop - entry)
        actual_dist = max(raw_dist, min_stop_dist)
        stop = entry + actual_dist

    target_r = float(p.get("target_r", 2.0))
    target = entry + actual_dist * target_r if side == "BUY" else entry - actual_dist * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 120)))
    evidence = DecisionEvidence(
        ("SMC_PRO_FVG_CONFIRMED",),
        {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "fvg_ce": fvg_target_ce,
            "sweep_price": sweep_ref_price,
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
        invalidation=Invalidation("SMC_SWEEP_INVALIDATION", stop, "Giá phá vỡ cấu trúc đáy/đỉnh quét thanh khoản"),
        evidence=evidence,
    )
