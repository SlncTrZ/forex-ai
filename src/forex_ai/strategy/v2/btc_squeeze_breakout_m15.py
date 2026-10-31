"""BTC Squeeze Breakout M15 Strategy — Volatility compression release with volume confirmation on BTCUSD M15.
Wing: code | Topic: btc_squeeze_breakout_m15 | Updated: 2026-10-31 11:30
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.market.indicators import atr, bollinger_bands, ema, sma
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
    tf = snapshot.timeframes.get(tf_name)
    if not tf or len(tf.closed_bars) < 35:
        evidence = DecisionEvidence(("INSUFFICIENT_CLOSED_BARS",), {})
        return StrategyResult(None, None, evidence, ("INSUFFICIENT_CLOSED_BARS",))

    bars = tf.closed_bars
    latest = bars[-1]
    prev = bars[-2]

    # 1. Bộ lọc Phiên: Chuyên biệt phiên Âu - Mỹ (06:30 - 16:30 UTC = 13:30 - 23:30 VN)
    t = now_utc.astimezone(UTC)
    hour = t.hour + t.minute / 60.0
    s_start = float(p.get("session_start_hour", 6.5))
    s_end = float(p.get("session_end_hour", 16.5))
    if t.hour in {21, 22} or hour < s_start or hour > s_end:
        evidence = DecisionEvidence(("SESSION_OR_ROLLOVER_BLACKOUT",), {"hour": hour, "start": s_start, "end": s_end})
        return StrategyResult(None, None, evidence, ("SESSION_OR_ROLLOVER_BLACKOUT",))

    # 2. Bộ lọc Xu hướng Lớn H1 (H1 Trend Bias: EMA 50 vs EMA 200)
    if bool(p.get("require_h1_trend", True)):
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

    # 3. Tính toán TTM Squeeze trên M15:
    # Bollinger Bands (20, 2.0) vs Keltner Channel (20, 1.5 ATR)
    bb_period = int(p.get("bb_period", 20))
    bb_mult = float(p.get("bb_mult", 2.0))
    kc_mult = float(p.get("kc_mult", 1.5))

    closes = [b.close for b in bars]
    bb_curr = bollinger_bands(closes, bb_period, bb_mult)
    bb_prev = bollinger_bands(closes[:-1], bb_period, bb_mult)

    atr_val = atr(bars[:-1], 14)
    atr_prev = atr(bars[:-2], 14)

    if not bb_curr or not bb_prev or atr_val <= 0 or atr_prev <= 0:
        evidence = DecisionEvidence(("INDICATOR_UNAVAILABLE",), {})
        return StrategyResult(None, None, evidence, ("INDICATOR_UNAVAILABLE",))

    kc_upper_curr = bb_curr.middle + kc_mult * atr_val
    kc_lower_curr = bb_curr.middle - kc_mult * atr_val

    kc_upper_prev = bb_prev.middle + kc_mult * atr_prev
    kc_lower_prev = bb_prev.middle - kc_mult * atr_prev

    squeeze_on_prev = (bb_prev.upper < kc_upper_prev) and (bb_prev.lower > kc_lower_prev)
    squeeze_on_curr = (bb_curr.upper < kc_upper_curr) and (bb_curr.lower > kc_lower_curr)

    # Momentum Histogram: giá đóng cửa so với trung bình (SMA20 + HL2)/2
    hl2 = (latest.high + latest.low) / 2.0
    mom = latest.close - (bb_curr.middle + hl2) / 2.0

    # 4. Nhận diện Squeeze Fire (Bung nén biến động)
    squeeze_fire = squeeze_on_prev and not squeeze_on_curr

    if not squeeze_fire:
        evidence = DecisionEvidence(
            ("NO_SQUEEZE_FIRE",),
            {"squeeze_on_prev": squeeze_on_prev, "squeeze_on_curr": squeeze_on_curr, "mom": mom},
        )
        return StrategyResult(None, None, evidence, ("NO_SQUEEZE_FIRE",))

    # 5. Bộ lọc Khối lượng bùng nổ (Volume Surge >= 1.15 SMA20)
    volumes = [float(b.volume) for b in bars]
    vol_sma = sma(volumes[:-1], 20)
    vol_ratio = latest.volume / max(vol_sma, 1.0)
    min_vol_ratio = float(p.get("min_volume_ratio", 1.15))

    if vol_ratio < min_vol_ratio:
        evidence = DecisionEvidence(("VOLUME_BELOW_THRESHOLD",), {"vol_ratio": vol_ratio, "min": min_vol_ratio})
        return StrategyResult(None, None, evidence, ("VOLUME_BELOW_THRESHOLD",))

    side: str | None = None
    if mom > 0 and latest.close > bb_curr.middle:
        side = "BUY"
    elif mom < 0 and latest.close < bb_curr.middle:
        side = "SELL"

    if side is None:
        evidence = DecisionEvidence(("NO_DIRECTIONAL_MOMENTUM",), {"mom": mom})
        return StrategyResult(None, None, evidence, ("NO_DIRECTIONAL_MOMENTUM",))

    # Kiểm tra H1 Trend Alignment
    if h1_trend in {"UP", "DOWN"}:
        if side == "BUY" and h1_trend != "UP":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))
        if side == "SELL" and h1_trend != "DOWN":
            evidence = DecisionEvidence(("H1_TREND_DISCORDANT",), {"side": side, "h1": h1_trend})
            return StrategyResult(None, None, evidence, ("H1_TREND_DISCORDANT",))

    # 6. Stop Loss & Take Profit
    from forex_ai.strategy.v2 import resolve_symbol_min_stop

    entry = snapshot.ask if side == "BUY" else snapshot.bid
    sl_atr_mult = float(p.get("sl_atr_multiplier", 1.5))
    raw_risk = sl_atr_mult * atr_val

    min_stop = resolve_symbol_min_stop(snapshot.symbol, float(p.get("min_stop_distance", 150.0)))
    risk = max(raw_risk, min_stop)
    max_sl = float(p.get("max_sl_distance", 2000.0))

    if risk > max_sl:
        evidence = DecisionEvidence(("RISK_EXCEEDS_MAX",), {"risk": risk, "max_allowed": max_sl})
        return StrategyResult(None, None, evidence, ("RISK_EXCEEDS_MAX",))

    stop = entry - risk if side == "BUY" else entry + risk
    target_r = float(p.get("target_r", 1.8))
    target = entry + risk * target_r if side == "BUY" else entry - risk * target_r

    expiry = now_utc + timedelta(minutes=int(p.get("expiry_minutes", 120)))
    evidence = DecisionEvidence(
        ("BTC_SQUEEZE_BREAKOUT_CONFIRMED",),
        {
            "side": side,
            "trigger": "SQUEEZE_FIRE_RELEASE",
            "entry": entry,
            "stop": stop,
            "target": target,
            "risk": risk,
            "target_r": target_r,
            "vol_ratio": vol_ratio,
            "mom": mom,
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
        invalidation=Invalidation("SQUEEZE_FAIL", stop, "Giá phá vỡ ngưỡng Stop Loss"),
        evidence=evidence,
    )
