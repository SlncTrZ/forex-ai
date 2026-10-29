"""Ichimoku Kinko Hyo Multi-Timeframe Strategy — H1/H4 Kumo Trend Bias + M15 Kijun Dynamic Bounce.
Wing: code | Topic: ichimoku_mtf | Updated: 2026-10-29 08:35
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.candles import candle_shape
from forex_ai.market.indicators import atr, ichimoku
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
    tf_name = str(p.get("decision_timeframe", "M15"))
    m15_tf = snapshot.timeframes.get(tf_name) or snapshot.timeframes.get("M15")
    h1_tf = snapshot.timeframes.get("H1")

    if not m15_tf or len(m15_tf.closed_bars) < 80:
        evidence = DecisionEvidence(("INSUFFICIENT_M15_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_M15_BARS",))

    if not h1_tf or len(h1_tf.closed_bars) < 80:
        evidence = DecisionEvidence(("INSUFFICIENT_H1_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_H1_BARS",))

    # 1. Session Gate: Tránh giờ Rollover (21:00 - 22:59 UTC)
    t = now_utc.astimezone(UTC)
    if t.hour in {21, 22}:
        evidence = DecisionEvidence(("ROLLOVER_HOUR_BLACKOUT",), {"hour": t.hour})
        return StrategyResult(None, None, evidence, ("ROLLOVER_HOUR_BLACKOUT",))

    # 2. H1 Ichimoku Trend Bias (Mây Kumo định hướng)
    h1_ichi = ichimoku(h1_tf.closed_bars)
    if not h1_ichi:
        evidence = DecisionEvidence(("H1_ICHIMOKU_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("H1_ICHIMOKU_UNAVAILABLE",))

    h1_bias = "NEUTRAL"
    if h1_ichi.price_relative_to_cloud == "ABOVE" and h1_ichi.cloud_color == "BULLISH":
        h1_bias = "BULLISH"
    elif h1_ichi.price_relative_to_cloud == "BELOW" and h1_ichi.cloud_color == "BEARISH":
        h1_bias = "BEARISH"

    if h1_bias == "NEUTRAL":
        evidence = DecisionEvidence(("H1_CHOP_INSIDE_CLOUD",), {"cloud_pos": h1_ichi.price_relative_to_cloud})
        return StrategyResult(None, None, evidence, ("H1_CHOP_INSIDE_CLOUD",))

    # 3. M15 Kijun Dynamic Bounce Trigger
    m15_bars = m15_tf.closed_bars
    latest = m15_bars[-1]
    prev = m15_bars[-2]
    m15_ichi = ichimoku(m15_bars)
    if not m15_ichi:
        evidence = DecisionEvidence(("M15_ICHIMOKU_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("M15_ICHIMOKU_UNAVAILABLE",))

    atr_val = atr(m15_bars[:-1], 14)
    if atr_val <= 0:
        evidence = DecisionEvidence(("INVALID_ATR",), {})
        return StrategyResult(None, None, evidence, ("INVALID_ATR",))

    side: str | None = None
    kijun_val = m15_ichi.kijun_sen

    # Kịch bản BUY: H1 Bullish, trên M15 giá chạm Kijun và rút chân tăng (Bounce)
    if h1_bias == "BULLISH":
        dist_to_kijun = abs(latest.close - kijun_val) / atr_val
        # Nến trước hoặc nến này chạm Kijun, và nến này đóng cửa xanh trên Kijun
        touched = (prev.low <= kijun_val + 0.2 * atr_val) or (latest.low <= kijun_val + 0.2 * atr_val)
        bounced = latest.close > kijun_val and latest.close > latest.open
        if touched and bounced and dist_to_kijun <= 1.2:
            side = "BUY"

    # Kịch bản SELL: H1 Bearish, trên M15 giá chạm Kijun và rút chân giảm (Reject)
    elif h1_bias == "BEARISH":
        dist_to_kijun = abs(latest.close - kijun_val) / atr_val
        touched = (prev.high >= kijun_val - 0.2 * atr_val) or (latest.high >= kijun_val - 0.2 * atr_val)
        rejected = latest.close < kijun_val and latest.close < latest.open
        if touched and rejected and dist_to_kijun <= 1.2:
            side = "SELL"

    if not side:
        evidence = DecisionEvidence(("NO_KIJUN_BOUNCE",), {"h1_bias": h1_bias, "kijun": kijun_val, "close": latest.close})
        return StrategyResult(None, None, evidence, ("NO_KIJUN_BOUNCE",))

    # 4. Entry & Stop Loss & Target:
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = 0.25 * atr_val
    min_stop_dist = float(p.get("min_stop_distance", 5.0 if "XAU" in snapshot.symbol.upper() else 0.0015))

    if side == "BUY":
        raw_stop = kijun_val - buffer
        raw_dist = abs(entry - raw_stop)
        actual_dist = max(raw_dist, min_stop_dist)
        stop = entry - actual_dist
    else:
        raw_stop = kijun_val + buffer
        raw_dist = abs(raw_stop - entry)
        actual_dist = max(raw_dist, min_stop_dist)
        stop = entry + actual_dist

    target_r = float(p.get("target_r", 2.0))
    target = entry + actual_dist * target_r if side == "BUY" else entry - actual_dist * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 180)))
    evidence = DecisionEvidence(
        ("ICHIMOKU_MTF_CONFIRMED",),
        {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "kijun": kijun_val,
            "h1_bias": h1_bias,
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
        invalidation=Invalidation("KIJUN_BREAK_FAIL", stop, "Giá phá thủng đường Kijun"),
        evidence=evidence,
    )
