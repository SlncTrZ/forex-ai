"""Keylevel & Trendline Breakout Strategy (V2 Module) — Bot tu dong bat cấn, SL/TP dong.

Wing: strategy | Topic: keylevel_trendline_breakout | Updated: 2026-10-30 20:15
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.market.indicators import atr, ema
from forex_ai.strategy.v1.contracts import (
    CandidateEnvelope,
    DecisionEvidence,
    Invalidation,
    MarketSnapshot,
    StrategyConfig,
    StrategyResult,
    build_candidate,
)
from forex_ai.strategy.v2.keylevel_detector import extract_structure

UTC = timezone.utc


def evaluate(snapshot: MarketSnapshot, config: StrategyConfig, now_utc: datetime) -> StrategyResult:
    p = config.parameters
    # Ho tro ca M15 va M30
    tf_name = str(p.get("decision_timeframe", "M30"))
    tf = snapshot.timeframes.get(tf_name)
    if not tf or len(tf.closed_bars) < 40:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prev = bars[-2]

    # 1. Bo loc Phien: Rollover blackout
    t = now_utc.astimezone(UTC)
    if t.hour in {21, 22}:
        evidence = DecisionEvidence(("ROLLOVER_BLACKOUT",), {"hour": t.hour})
        return StrategyResult(None, None, evidence, ("ROLLOVER_BLACKOUT",))

    # 2. Bo loc Trend Bias tren H1 (EMA 50 / 200 tren H1)
    h1_tf = snapshot.timeframes.get("H1")
    if h1_tf and len(h1_tf.closed_bars) >= 50:
        h1_closes = [b.close for b in h1_tf.closed_bars]
        h1_fast = ema(h1_closes, 50)
        h1_slow = ema(h1_closes, 200)
        h1_bias = "UP" if h1_fast > h1_slow else "DOWN"
    else:
        h1_bias = "UNKNOWN"

    # 3. Bot tu dong tinh toan Keylevel va Trendline
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    left_bars = int(p.get("left_bars", 15))
    right_bars = int(p.get("right_bars", 5))
    struct = extract_structure(highs, lows, left_bars=left_bars, right_bars=right_bars)

    if struct.resistance is None or struct.support is None:
        evidence = DecisionEvidence(("NO_KEYLEVEL_FOUND",), {})
        return StrategyResult(None, None, evidence, ("NO_KEYLEVEL_FOUND",))

    side: str | None = None
    trigger_type = "NONE"

    # A. Pha vo Keylevel ngang (Horizontal Support / Resistance Breakout)
    curr_bar_idx = len(bars) - 1
    if prev.close <= struct.resistance and latest.close > struct.resistance and h1_bias in ("UP", "UNKNOWN"):
        side = "BUY"
        trigger_type = "KEYLEVEL_RESISTANCE_BREAKOUT"
    elif prev.close >= struct.support and latest.close < struct.support and h1_bias in ("DOWN", "UNKNOWN"):
        side = "SELL"
        trigger_type = "KEYLEVEL_SUPPORT_BREAKOUT"

    # B. Pha vo Trendline cheo (Trendline Breakout)
    if side is None:
        if struct.resistance_trendline is not None and h1_bias in ("UP", "UNKNOWN"):
            tl_price = struct.resistance_trendline.price_at(curr_bar_idx)
            if prev.close <= tl_price and latest.close > tl_price:
                side = "BUY"
                trigger_type = "TRENDLINE_RESISTANCE_BREAKOUT"
        elif struct.support_trendline is not None and h1_bias in ("DOWN", "UNKNOWN"):
            tl_price = struct.support_trendline.price_at(curr_bar_idx)
            if prev.close >= tl_price and latest.close < tl_price:
                side = "SELL"
                trigger_type = "TRENDLINE_SUPPORT_BREAKOUT"

    if side is None:
        evidence = DecisionEvidence(("NO_BREAKOUT_SIGNAL",), {"res": struct.resistance, "sup": struct.support})
        return StrategyResult(None, None, evidence, ("NO_BREAKOUT_SIGNAL",))

    # 4. Tinh toan SL/TP DONG THEO KEYLEVEL THUC TE
    atr_val = atr(bars[:-1], 14)
    min_dist = max(5.0 if "XAU" in snapshot.symbol else (500.0 if "BTC" in snapshot.symbol else 0.0015), atr_val)

    if side == "BUY":
        entry = latest.close
        sl = min(struct.support, entry - min_dist)
        sl_dist = entry - sl
        # TP dong lay theo bien do swing doi dien hoac 2.0 * SL
        tp_dist = max(struct.resistance - struct.support if struct.resistance > struct.support else 0, sl_dist * 2.0)
        tp = entry + tp_dist
    else:
        entry = latest.close
        sl = max(struct.resistance, entry + min_dist)
        sl_dist = sl - entry
        tp_dist = max(struct.resistance - struct.support if struct.resistance > struct.support else 0, sl_dist * 2.0)
        tp = entry - tp_dist

    evidence = DecisionEvidence(
        ("KEYLEVEL_SIGNAL_CONFIRMED",),
        {
            "side": side,
            "trigger_type": trigger_type,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "resistance": struct.resistance,
            "support": struct.support,
            "h1_bias": h1_bias,
        },
    )

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 120)))
    candidate = build_candidate(
        snapshot=snapshot,
        strategy_id=config.version.strategy_id,
        side=side,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        expires_at=expiry,
        evidence=evidence,
        now_utc=now_utc,
    )

    return StrategyResult(candidate, None, evidence, ("KEYLEVEL_SIGNAL_CONFIRMED",))
