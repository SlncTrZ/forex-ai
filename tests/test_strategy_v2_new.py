"""Unit tests for SMC Pro FVG, Ichimoku MTF, and MTF Trend Continuation strategies.
Wing: code | Topic: test_strategy_v2_new | Updated: 2026-10-29 08:50
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.strategy.v1.contracts import (
    Candle,
    MarketSnapshot,
    StrategyConfig,
    StrategyVersion,
    TimeframeSnapshot,
)
from forex_ai.strategy.v2 import (
    ichimoku_mtf,
    mtf_trend_continuation,
    smc_pro_fvg,
)

UTC = timezone.utc


def _make_series(n: int = 60, start_price: float = 2700.0, step: float = 0.5, base_time: datetime | None = None) -> list[Candle]:
    t0 = base_time or datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
    bars = []
    price = start_price
    for i in range(n):
        t = t0 + timedelta(minutes=5 * i)
        bars.append(
            Candle(
                time_utc=t,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + step,
                volume=100.0,
            )
        )
        price += step
    return bars


def test_smc_pro_fvg_insufficient_bars():
    cfg = StrategyConfig(StrategyVersion("smc_pro_fvg_v2", "2.0.0"), {})
    now = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2700.0,
        ask=2700.2,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(_make_series(10)))},
    )
    res = smc_pro_fvg.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "INSUFFICIENT_CLOSED_BARS" in res.no_setup_reason_codes


def test_ichimoku_mtf_rejects_insufficient_h1_bars():
    cfg = StrategyConfig(StrategyVersion("ichimoku_mtf_v2", "2.0.0"), {})
    now = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2700.0,
        ask=2700.2,
        timeframes={
            "M15": TimeframeSnapshot("M15", tuple(_make_series(90, base_time=now - timedelta(minutes=15 * 95)))),
            "H1": TimeframeSnapshot("H1", tuple(_make_series(30, base_time=now - timedelta(hours=35)))),  # Cần ít nhất 80 nến
        },
    )
    res = ichimoku_mtf.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "INSUFFICIENT_H1_BARS" in res.no_setup_reason_codes


def test_mtf_trend_continuation_rejects_low_adx():
    cfg = StrategyConfig(StrategyVersion("mtf_trend_continuation_v2", "2.0.0"), {"min_h1_adx": 30.0})
    now = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    # Series giá đi ngang ziczac để ADX thấp
    bars_h1 = []
    t_start = now - timedelta(hours=65)
    for i in range(60):
        t = t_start + timedelta(hours=i)
        p = 2700.0 + (0.5 if i % 2 == 0 else -0.5)
        bars_h1.append(Candle(time_utc=t, open=p, high=p + 0.8, low=p - 0.8, close=p, volume=100.0))

    bars_m5 = _make_series(60, start_price=2700.0, step=0.05, base_time=now - timedelta(minutes=305))

    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2703.0,
        ask=2703.2,
        timeframes={
            "M5": TimeframeSnapshot("M5", tuple(bars_m5)),
            "H1": TimeframeSnapshot("H1", tuple(bars_h1)),
        },
    )
    res = mtf_trend_continuation.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "H1_LOW_ADX_CHOP" in res.no_setup_reason_codes
