from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean
from typing import Protocol, Sequence


class OHLCV(Protocol):
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class BollingerBands:
    middle: float
    upper: float
    lower: float
    bandwidth: float
    percent_b: float | None


@dataclass(frozen=True)
class StochasticValue:
    percent_k: float
    percent_d: float


@dataclass(frozen=True)
class DMIADX:
    plus_di: float
    minus_di: float
    adx: float


def _require_period(period: int) -> None:
    if period <= 0:
        raise ValueError("period must be positive")


def sma(values: Sequence[float], period: int) -> float:
    _require_period(period)
    if len(values) < period:
        raise ValueError(f"need at least {period} values")
    window = [float(value) for value in values[-period:]]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float:
    _require_period(period)
    if not values:
        raise ValueError("no values")
    alpha = 2.0 / (period + 1.0)
    out = float(values[0])
    for value in values[1:]:
        out = alpha * float(value) + (1.0 - alpha) * out
    return out


def atr(bars: Sequence[OHLCV], period: int) -> float:
    _require_period(period)
    if len(bars) < 2:
        return 0.0
    true_ranges = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[:-1], bars[1:])
    ]
    window = true_ranges[-period:]
    return sum(window) / len(window) if window else 0.0


def trend_state(bars: Sequence[OHLCV], fast: int, slow: int) -> str:
    _require_period(fast)
    _require_period(slow)
    if fast >= slow:
        raise ValueError("fast period must be smaller than slow period")
    if len(bars) < slow:
        return "UNAVAILABLE"
    closes = [float(bar.close) for bar in bars]
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    close = closes[-1]
    if close > fast_ema > slow_ema:
        return "UP"
    if close < fast_ema < slow_ema:
        return "DOWN"
    return "MIXED"


def efficiency(bars: Sequence[OHLCV], window: int) -> float:
    _require_period(window)
    if len(bars) < window + 1:
        return 0.0
    closes = [float(bar.close) for bar in bars[-(window + 1):]]
    gross = sum(abs(right - left) for left, right in zip(closes[:-1], closes[1:]))
    return 0.0 if gross == 0 else abs(closes[-1] - closes[0]) / gross


def rsi(values: Sequence[float], period: int) -> float | None:
    """Return Wilder RSI for the latest value."""
    _require_period(period)
    if len(values) < period + 1:
        return None
    deltas = [float(right) - float(left) for left, right in zip(values[:-1], values[1:])]
    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = ((period - 1) * avg_gain + gain) / period
        avg_loss = ((period - 1) * avg_loss + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def bollinger_bands(values: Sequence[float], period: int, stddev_multiplier: float) -> BollingerBands | None:
    _require_period(period)
    if stddev_multiplier <= 0:
        raise ValueError("stddev_multiplier must be positive")
    if len(values) < period:
        return None
    window = [float(value) for value in values[-period:]]
    middle = mean(window)
    variance = sum((value - middle) ** 2 for value in window) / period
    deviation = sqrt(variance)
    upper = middle + stddev_multiplier * deviation
    lower = middle - stddev_multiplier * deviation
    bandwidth = 0.0 if middle == 0 else (upper - lower) / abs(middle)
    width = upper - lower
    percent_b = None if width == 0 else (window[-1] - lower) / width
    return BollingerBands(middle, upper, lower, bandwidth, percent_b)


def stochastic(bars: Sequence[OHLCV], k_period: int, d_period: int) -> StochasticValue | None:
    _require_period(k_period)
    _require_period(d_period)
    required = k_period + d_period - 1
    if len(bars) < required:
        return None
    k_values: list[float] = []
    start = len(bars) - d_period
    for end_index in range(start, len(bars)):
        window_start = end_index - k_period + 1
        window = bars[window_start:end_index + 1]
        highest = max(float(bar.high) for bar in window)
        lowest = min(float(bar.low) for bar in window)
        close = float(bars[end_index].close)
        width = highest - lowest
        k_values.append(50.0 if width == 0 else 100.0 * (close - lowest) / width)
    return StochasticValue(k_values[-1], sum(k_values) / len(k_values))


def dmi_adx(bars: Sequence[OHLCV], period: int) -> DMIADX | None:
    """Return Wilder +DI, -DI and ADX for the latest bar."""
    _require_period(period)
    if len(bars) < (period * 2):
        return None

    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for prev, cur in zip(bars[:-1], bars[1:]):
        up_move = float(cur.high) - float(prev.high)
        down_move = float(prev.low) - float(cur.low)
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        trs.append(max(float(cur.high) - float(cur.low), abs(float(cur.high) - float(prev.close)), abs(float(cur.low) - float(prev.close))))

    tr_smooth = sum(trs[:period])
    plus_smooth = sum(plus_dm[:period])
    minus_smooth = sum(minus_dm[:period])
    dx_values: list[float] = []
    latest_plus_di = latest_minus_di = 0.0

    for index in range(period - 1, len(trs)):
        if index >= period:
            tr_smooth = tr_smooth - (tr_smooth / period) + trs[index]
            plus_smooth = plus_smooth - (plus_smooth / period) + plus_dm[index]
            minus_smooth = minus_smooth - (minus_smooth / period) + minus_dm[index]
        if tr_smooth <= 0:
            latest_plus_di = latest_minus_di = 0.0
            dx = 0.0
        else:
            latest_plus_di = 100.0 * plus_smooth / tr_smooth
            latest_minus_di = 100.0 * minus_smooth / tr_smooth
            denominator = latest_plus_di + latest_minus_di
            dx = 0.0 if denominator == 0 else 100.0 * abs(latest_plus_di - latest_minus_di) / denominator
        dx_values.append(dx)

    if len(dx_values) < period:
        return None
    adx_value = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx_value = ((period - 1) * adx_value + dx) / period
    return DMIADX(latest_plus_di, latest_minus_di, adx_value)


def volume_zscore(bars: Sequence[OHLCV], period: int) -> float | None:
    """Z-score of the latest bar volume; for MT5 Forex this is a tick-volume proxy."""
    _require_period(period)
    if len(bars) < period:
        return None
    values = [float(bar.volume) for bar in bars[-period:]]
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / period
    deviation = sqrt(variance)
    return 0.0 if deviation == 0 else (values[-1] - avg) / deviation
