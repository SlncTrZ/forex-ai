from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GeneratedSignal:
    signal_key: str
    symbol: str
    strategy: str
    direction: str
    score: float
    proposed_entry: float | None
    proposed_sl: float | None
    proposed_tp: float | None
    rr: float | None
    market_time_msc: int | None
    evidence: dict[str, Any]


def _closed(context: dict[str, Any], tf: str) -> dict[str, Any]:
    return context.get("timeframes", {}).get(tf, {}).get("closed_features", {}) or {}


def _candle_time(context: dict[str, Any], tf: str) -> int | None:
    recent = _closed(context, tf).get("recent_candles") or []
    if not recent:
        return None
    value = recent[-1].get("time")
    return int(value) if isinstance(value, (int, float)) else None


def _score_direction(context: dict[str, Any], direction: str) -> tuple[float, dict[str, Any]]:
    m5 = _closed(context, "M5")
    m15 = _closed(context, "M15")
    h1 = _closed(context, "H1")
    h4 = _closed(context, "H4")
    wanted = "up" if direction == "BUY" else "down"

    score = 0.0
    evidence: dict[str, Any] = {
        "direction": direction,
        "m5_trend": m5.get("trend"),
        "m15_trend": m15.get("trend"),
        "h1_trend": h1.get("trend"),
        "h4_trend": h4.get("trend"),
        "m15_adx14": m15.get("adx14"),
        "h1_adx14": h1.get("adx14"),
        "m15_rsi14": m15.get("rsi14"),
        "m15_breakout_20": m15.get("breakout_20"),
        "m15_distance_ema20_atr": m15.get("distance_ema20_atr"),
    }

    if h1.get("trend") == wanted:
        score += 0.25
    if h4.get("trend") == wanted:
        score += 0.15
    elif h4.get("trend") == "mixed":
        score += 0.05
    if m15.get("trend") == wanted:
        score += 0.20
    if m5.get("trend") == wanted:
        score += 0.10

    m15_adx = m15.get("adx14")
    h1_adx = h1.get("adx14")
    if isinstance(m15_adx, (int, float)) and m15_adx >= 18:
        score += 0.10
    if isinstance(h1_adx, (int, float)) and h1_adx >= 18:
        score += 0.05

    rsi = m15.get("rsi14")
    if isinstance(rsi, (int, float)):
        if direction == "BUY" and 42 <= rsi <= 68:
            score += 0.08
        if direction == "SELL" and 32 <= rsi <= 58:
            score += 0.08

    breakout = m15.get("breakout_20")
    if (direction == "BUY" and breakout == "up") or (direction == "SELL" and breakout == "down"):
        score += 0.12

    return min(score, 1.0), evidence


def generate_signal(context: dict[str, Any], threshold: float = 0.65) -> GeneratedSignal | None:
    symbol = str(context.get("symbol") or "")
    if not symbol:
        return None

    buy_score, buy_evidence = _score_direction(context, "BUY")
    sell_score, sell_evidence = _score_direction(context, "SELL")
    if max(buy_score, sell_score) < threshold:
        return None

    direction = "BUY" if buy_score > sell_score else "SELL"
    score = buy_score if direction == "BUY" else sell_score
    evidence = buy_evidence if direction == "BUY" else sell_evidence
    m15 = _closed(context, "M15")

    breakout = m15.get("breakout_20")
    distance = m15.get("distance_ema20_atr")
    if (direction == "BUY" and breakout == "up") or (direction == "SELL" and breakout == "down"):
        strategy = "trend_breakout"
    elif isinstance(distance, (int, float)) and abs(distance) <= 0.80:
        strategy = "trend_pullback"
    else:
        strategy = "trend_continuation"

    tick = context.get("tick", {})
    entry = tick.get("ask") if direction == "BUY" else tick.get("bid")
    atr = m15.get("atr14")
    sl = tp = rr = None
    if isinstance(entry, (int, float)) and isinstance(atr, (int, float)) and atr > 0:
        stop_distance = 1.5 * atr
        target_distance = 3.0 * atr
        if direction == "BUY":
            sl = entry - stop_distance
            tp = entry + target_distance
        else:
            sl = entry + stop_distance
            tp = entry - target_distance
        rr = target_distance / stop_distance

    closed_time = _candle_time(context, "M15")
    raw_key = f"{symbol}|{strategy}|{direction}|{closed_time}"
    signal_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
    evidence.update(
        {
            "buy_score": buy_score,
            "sell_score": sell_score,
            "threshold": threshold,
            "m15_closed_time": closed_time,
            "decision_clock": context.get("decision_clock"),
        }
    )

    return GeneratedSignal(
        signal_key=signal_key,
        symbol=symbol,
        strategy=strategy,
        direction=direction,
        score=round(score, 4),
        proposed_entry=float(entry) if isinstance(entry, (int, float)) else None,
        proposed_sl=float(sl) if sl is not None else None,
        proposed_tp=float(tp) if tp is not None else None,
        rr=float(rr) if rr is not None else None,
        market_time_msc=int(tick["time_msc"]) if isinstance(tick.get("time_msc"), (int, float)) else None,
        evidence=evidence,
    )
