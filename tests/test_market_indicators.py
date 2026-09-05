from __future__ import annotations

from dataclasses import dataclass

import pytest

from forex_ai.market.indicators import bollinger_bands, dmi_adx, rsi, stochastic, volume_zscore


@dataclass(frozen=True)
class Bar:
    high: float
    low: float
    close: float
    volume: float


def _trend_bars(count: int, step: float = 1.0) -> tuple[Bar, ...]:
    out = []
    close = 100.0
    for i in range(count):
        close += step
        out.append(Bar(close + 0.5, close - 0.5, close, 100 + i))
    return tuple(out)


def test_rsi_detects_persistent_upward_momentum():
    values = [float(i) for i in range(1, 40)]
    assert rsi(values, 14) == pytest.approx(100.0)


def test_bollinger_bands_expose_bandwidth_and_percent_b():
    bands = bollinger_bands([1, 2, 3, 4, 5, 6], 5, 2.0)
    assert bands is not None
    assert bands.upper > bands.middle > bands.lower
    assert bands.bandwidth > 0
    assert bands.percent_b is not None and bands.percent_b > 0.5


def test_stochastic_close_near_range_high_is_high_percent_k():
    bars = _trend_bars(20)
    value = stochastic(bars, 14, 3)
    assert value is not None
    assert value.percent_k > 90
    assert value.percent_d > 80


def test_dmi_adx_identifies_strong_directional_series():
    result = dmi_adx(_trend_bars(40), 14)
    assert result is not None
    assert result.plus_di > result.minus_di
    assert result.adx > 20


def test_volume_zscore_flags_latest_volume_spike():
    bars = [Bar(2, 1, 1.5, 100) for _ in range(19)] + [Bar(2, 1, 1.5, 1000)]
    score = volume_zscore(tuple(bars), 20)
    assert score is not None and score > 3
