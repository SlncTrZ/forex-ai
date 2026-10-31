"""Range Liquidity Trap M5 Strategy — Counter-attack false breakouts of range boundaries in Sideway markets.
Wing: code | Topic: range_liquidity_trap_m5 | Updated: 2026-10-31 20:00
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.market.indicators import atr
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
    tf = snapshot.timeframes.get(tf_name)
    lookback = int(p.get("range_lookback_bars", 20))
    if not tf or len(tf.closed_bars) < lookback + 10:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]

    # 1. Bộ lọc Phiên: London & NY sessions (06:30 - 16:30 UTC = 13:30 - 23:30 VN)
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 6.5))
    s_end = float(p.get("session_end_hour", 16.5))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour, "start": s_start, "end": s_end})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    # 2. Tính toán ATR(14) trên các nến đã đóng trước đó
    atr_val = atr(bars[:-1], 14)
    if atr_val <= 0:
        evidence = DecisionEvidence(("INDICATOR_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("INDICATOR_UNAVAILABLE",))

    # 3. Xác định biên độ Range trong [lookback] nến trước nến latest
    prior_bars = bars[-lookback-1:-1]
    range_high = max(b.high for b in prior_bars)
    range_low = min(b.low for b in prior_bars)
    range_span = range_high - range_low

    # Range phải có biên độ đủ rộng (> 0.8 ATR) và không quá bung rộng (> 6.0 ATR)
    if range_span < 0.8 * atr_val or range_span > 6.0 * atr_val:
        evidence = DecisionEvidence(("INVALID_RANGE_SPAN",), {"range_span": range_span, "atr": atr_val})
        return StrategyResult(None, None, evidence, ("INVALID_RANGE_SPAN",))

    # 4. Nhận diện False Breakout / Liquidity Trap
    candle_range = latest.high - latest.low
    if candle_range <= 0:
        evidence = DecisionEvidence(("ZERO_RANGE_CANDLE",), {})
        return StrategyResult(None, None, evidence, ("ZERO_RANGE_CANDLE",))

    side: str | None = None
    trap_level: float = 0.0

    # Kịch bản SELL TRAP: Giá chọc thủng vượt Range High nhưng đóng nến đỏ quay ngược vào trong Range
    if latest.high > range_high and latest.close < range_high and latest.close < latest.open:
        upper_wick = latest.high - max(latest.open, latest.close)
        if upper_wick >= 0.25 * candle_range:
            side = "SELL"
            trap_level = latest.high

    # Kịch bản BUY TRAP: Giá chọc thủng Range Low nhưng đóng nến xanh quay ngược vào trong Range
    elif latest.low < range_low and latest.close > range_low and latest.close > latest.open:
        lower_wick = min(latest.open, latest.close) - latest.low
        if lower_wick >= 0.25 * candle_range:
            side = "BUY"
            trap_level = latest.low

    if side is None:
        evidence = DecisionEvidence(("NO_LIQUIDITY_TRAP_SETUP",), {"range_high": range_high, "range_low": range_low})
        return StrategyResult(None, None, evidence, ("NO_LIQUIDITY_TRAP_SETUP",))

    # 5. Định hình Stop Loss & Take Profit
    entry = snapshot.ask if side == "BUY" else snapshot.bid
    buffer = float(p.get("buffer_atr", 0.1)) * atr_val

    if side == "BUY":
        raw_stop = trap_level - buffer
        raw_risk = entry - raw_stop
    else:
        raw_stop = trap_level + buffer
        raw_risk = raw_stop - entry

    from forex_ai.strategy.v2 import resolve_symbol_min_stop
    min_stop_dist = resolve_symbol_min_stop(snapshot.symbol, float(p.get("min_stop_distance", 5.0)))
    risk = max(raw_risk, min_stop_dist)

    # Ràng buộc rủi ro tối thiểu 0.2 ATR, tối đa 4.0 ATR
    if risk < 0.2 * atr_val or risk > 4.0 * atr_val:
        evidence = DecisionEvidence(("INVALID_RISK_RANGE",), {"risk": risk, "atr": atr_val})
        return StrategyResult(None, None, evidence, ("INVALID_RISK_RANGE",))

    if side == "BUY":
        stop = entry - risk
    else:
        stop = entry + risk

    target_r = float(p.get("target_r", 2.0))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 60)))
    evidence = DecisionEvidence(
        ("RANGE_LIQUIDITY_TRAP_CONFIRMED",),
        {
            "side": side,
            "entry": entry,
            "stop": stop,
            "target": target,
            "range_high": range_high,
            "range_low": range_low,
            "range_span": range_span,
            "trap_level": trap_level,
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
        invalidation=Invalidation("RANGE_BOUNDARY_VIOLATED", stop, "Giá phá vỡ đầu râu nến quét thanh khoản"),
        evidence=evidence,
        no_setup_reason_codes=(),
    )
