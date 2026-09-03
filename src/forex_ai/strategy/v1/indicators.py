from __future__ import annotations

from .contracts import Candle


def ema(values: list[float], period: int) -> float:
    if not values:
        raise ValueError("no values")
    alpha = 2.0 / (period + 1.0)
    out = values[0]
    for value in values[1:]:
        out = alpha * value + (1.0 - alpha) * out
    return out


def atr(bars: tuple[Candle, ...], period: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    true_ranges = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[:-1], bars[1:])
    ]
    window = true_ranges[-period:]
    return sum(window) / len(window) if window else 0.0


def trend_state(bars: tuple[Candle, ...], fast: int = 20, slow: int = 50) -> str:
    if len(bars) < slow:
        return "UNAVAILABLE"
    closes = [bar.close for bar in bars]
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    close = closes[-1]
    if close > fast_ema > slow_ema:
        return "UP"
    if close < fast_ema < slow_ema:
        return "DOWN"
    return "MIXED"


def efficiency(bars: tuple[Candle, ...], window: int = 14) -> float:
    if len(bars) < window + 1:
        return 0.0
    closes = [bar.close for bar in bars[-(window + 1):]]
    gross = sum(abs(right - left) for left, right in zip(closes[:-1], closes[1:]))
    return 0.0 if gross == 0 else abs(closes[-1] - closes[0]) / gross
