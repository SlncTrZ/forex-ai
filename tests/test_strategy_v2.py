"""Unit tests for Pro Volatility Expansion V2, Supertrend Volume M5, and Supertrend Stochastic M5 strategies.
Wing: code | Topic: test_strategy_v2 | Updated: 2026-10-30 20:37
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forex_ai.strategy.v1.contracts import (
    Candle,
    MarketSnapshot,
    StrategyConfig,
    StrategyVersion,
    TimeframeSnapshot,
)
from forex_ai.strategy.v2 import (
    btc_squeeze_breakout_m15,
    pro_volatility_expansion_v2,
    supertrend_stochastic_m5,
    supertrend_volume_m5,
)

UTC = timezone.utc


def _make_bars(n: int = 50, start_price: float = 2700.0, step: float = 0.5, base_time: datetime | None = None) -> list[Candle]:
    t0 = base_time or datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
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


def test_pro_volatility_v2_rejects_insufficient_bars():
    cfg = StrategyConfig(StrategyVersion("pro_volatility_expansion_v2", "2.0.0"), {})
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2700.0,
        ask=2700.2,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(_make_bars(10, base_time=now - timedelta(minutes=60))))},
    )
    res = pro_volatility_expansion_v2.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "INSUFFICIENT_CLOSED_BARS" in res.no_setup_reason_codes


def test_pro_volatility_v2_rejects_rollover_hour():
    cfg = StrategyConfig(StrategyVersion("pro_volatility_expansion_v2", "2.0.0"), {})
    rollover_time = datetime(2026, 10, 28, 22, 15, tzinfo=UTC)
    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=rollover_time,
        market_time_msc=int(rollover_time.timestamp() * 1000),
        bid=2700.0,
        ask=2700.2,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(_make_bars(50, base_time=rollover_time - timedelta(minutes=260))))},
    )
    res = pro_volatility_expansion_v2.evaluate(snap, cfg, rollover_time)
    assert res.candidate is None
    assert "ROLLOVER_HOUR_BLACKOUT" in res.evidence.reason_codes


def test_pro_volatility_v2_confirms_valid_bullish_displacement():
    cfg = StrategyConfig(
        StrategyVersion("pro_volatility_expansion_v2", "2.0.0"),
        {"min_body_ratio": 0.60, "min_range_atr": 1.1, "min_volume_zscore": 0.5, "target_r": 2.0},
    )
    now = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
    # 39 nến trước
    bars = _make_bars(39, start_price=2700.0, step=0.1, base_time=now - timedelta(minutes=200))
    # Nến thứ 40 bứt phá
    last_t = now - timedelta(minutes=5)
    bars.append(
        Candle(
            time_utc=last_t,
            open=2705.0,
            high=2708.5,
            low=2704.8,
            close=2708.2,
            volume=500.0,
        )
    )

    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2708.0,
        ask=2708.4,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(bars))},
    )

    res = pro_volatility_expansion_v2.evaluate(snap, cfg, now)
    assert res.candidate is not None
    assert res.candidate.side == "BUY"
    assert res.candidate.take_profit > res.candidate.reference_entry
    assert res.candidate.stop_loss < res.candidate.reference_entry
    assert "PRO_VOLATILITY_EXPANSION_CONFIRMED" in res.evidence.reason_codes


def test_supertrend_volume_m5_rejects_insufficient_volume():
    cfg = StrategyConfig(
        StrategyVersion("supertrend_volume_m5", "2.0.0"),
        {"supertrend_period": 5, "supertrend_multiplier": 2.0, "min_volume_ratio": 2.5},  # Yêu cầu gấp 2.5x volume
    )
    now = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
    bars = _make_bars(39, start_price=2720.0, step=-0.2, base_time=now - timedelta(minutes=200))
    # Nến 40 flip nhưng volume chỉ 50.0 (thấp hơn trung bình 100.0)
    last_t = now - timedelta(minutes=5)
    bars.append(
        Candle(
            time_utc=last_t,
            open=2712.0,
            high=2718.0,
            low=2711.8,
            close=2717.5,
            volume=50.0,
        )
    )

    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2717.3,
        ask=2717.7,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(bars))},
    )

    res = supertrend_volume_m5.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "VOLUME_BELOW_THRESHOLD" in res.evidence.reason_codes


def test_supertrend_volume_m5_confirms_trend_flip():
    cfg = StrategyConfig(
        StrategyVersion("supertrend_volume_m5", "2.0.0"),
        {"supertrend_period": 5, "supertrend_multiplier": 2.0, "min_volume_ratio": 1.0, "target_r": 1.5},
    )
    now = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)
    # 39 nến giảm nhẹ
    bars = _make_bars(39, start_price=2720.0, step=-0.2, base_time=now - timedelta(minutes=200))
    # Nến thứ 40 tăng vừa đủ để flip Supertrend
    last_t = now - timedelta(minutes=5)
    bars.append(
        Candle(
            time_utc=last_t,
            open=2712.0,
            high=2718.0,
            low=2711.8,
            close=2717.5,
            volume=300.0,
        )
    )

    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2717.3,
        ask=2717.7,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(bars))},
    )

    res = supertrend_volume_m5.evaluate(snap, cfg, now)
    assert res.candidate is not None
    assert res.candidate.side == "BUY"
    assert res.candidate.stop_loss < res.candidate.reference_entry
    assert res.candidate.take_profit > res.candidate.reference_entry
    assert "SUPERTREND_VOLUME_CONFIRMED" in res.evidence.reason_codes


def test_supertrend_stochastic_m5_confirms_buy():
    cfg = StrategyConfig(
        StrategyVersion("supertrend_stochastic_m5", "2.0.0"),
        {
            "supertrend_period": 10,
            "supertrend_multiplier": 3.0,
            "stoch_k_period": 7,
            "stoch_d_period": 3,
            "stoch_oversold": 20.0,
            "trigger_mode": "CROSS_DOWN",
            "sl_buffer": 3.0,
            "target_r": 1.5,
        },
    )
    now = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)
    # 50 nến tăng nhẹ từ 2675 lên 2700 để Supertrend vững ở mức ~2692
    bars = _make_bars(50, start_price=2675.0, step=0.5, base_time=now - timedelta(minutes=300))
    # Nến 51 và 52 hồi nhẹ để Stochastic %K trong 7 nến gần nhất rơi xuống < 20
    last_t = now - timedelta(minutes=5)
    prev_t = now - timedelta(minutes=10)
    bars.append(
        Candle(
            time_utc=prev_t,
            open=2700.0,
            high=2701.0,
            low=2699.0,
            close=2699.8,
            volume=100.0,
        )
    )
    bars.append(
        Candle(
            time_utc=last_t,
            open=2699.8,
            high=2700.2,
            low=2697.0,
            close=2697.2,
            volume=120.0,
        )
    )

    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2697.0,
        ask=2697.4,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(bars))},
    )

    res = supertrend_stochastic_m5.evaluate(snap, cfg, now)
    assert res.candidate is not None
    assert res.candidate.side == "BUY"
    assert res.candidate.stop_loss < res.candidate.reference_entry
    assert res.candidate.take_profit > res.candidate.reference_entry
    assert "SUPERTREND_STOCHASTIC_CONFIRMED" in res.evidence.reason_codes
    assert res.evidence.values["trigger"] in {"STOCH_CROSS_DOWN_20", "STOCH_TOUCH_20"}


def test_supertrend_stochastic_m5_confirms_sell():
    cfg = StrategyConfig(
        StrategyVersion("supertrend_stochastic_m5", "2.0.0"),
        {
            "supertrend_period": 10,
            "supertrend_multiplier": 3.0,
            "stoch_k_period": 7,
            "stoch_d_period": 3,
            "stoch_overbought": 80.0,
            "trigger_mode": "CROSS_DOWN",
            "sl_buffer": 3.0,
            "target_r": 1.5,
        },
    )
    now = datetime(2026, 10, 30, 14, 0, tzinfo=UTC)
    # 50 nến giảm nhẹ từ 2725 xuống 2700 để Supertrend vững ở xu hướng DOWN ~2708
    bars = _make_bars(50, start_price=2725.0, step=-0.5, base_time=now - timedelta(minutes=300))
    # Nến 51 và 52 hồi tăng nhẹ để Stochastic %K trong 7 nến gần nhất vọt lên >= 80
    last_t = now - timedelta(minutes=5)
    prev_t = now - timedelta(minutes=10)
    bars.append(
        Candle(
            time_utc=prev_t,
            open=2700.0,
            high=2701.0,
            low=2699.0,
            close=2700.2,
            volume=100.0,
        )
    )
    bars.append(
        Candle(
            time_utc=last_t,
            open=2700.2,
            high=2703.0,
            low=2699.8,
            close=2702.8,
            volume=120.0,
        )
    )

    snap = MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=2702.6,
        ask=2703.0,
        timeframes={"M5": TimeframeSnapshot("M5", tuple(bars))},
    )

    res = supertrend_stochastic_m5.evaluate(snap, cfg, now)
    assert res.candidate is not None
    assert res.candidate.side == "SELL"
    assert res.candidate.stop_loss > res.candidate.reference_entry
    assert res.candidate.take_profit < res.candidate.reference_entry
    assert "SUPERTREND_STOCHASTIC_CONFIRMED" in res.evidence.reason_codes
    assert res.evidence.values["trigger"] in {"STOCH_CROSS_UP_80", "STOCH_TOUCH_80"}


def test_btc_squeeze_breakout_m15_confirms_buy():
    cfg = StrategyConfig(
        StrategyVersion("btc_squeeze_breakout_m15", "2.0.0"),
        {
            "decision_timeframe": "M15",
            "bb_period": 10,
            "bb_mult": 1.5,
            "kc_mult": 2.0,
            "min_volume_ratio": 1.0,
            "sl_atr_multiplier": 1.5,
            "target_r": 1.8,
            "require_h1_trend": False,
            "session_start_hour": 0.0,
            "session_end_hour": 24.0,
        },
    )
    now = datetime(2026, 10, 31, 14, 0, tzinfo=UTC)
    # 40 nến dao động hẹp quanh 75000 để tạo Squeeze On (BB < KC)
    bars = []
    base_t = now - timedelta(minutes=15 * 45)
    for i in range(40):
        t = base_t + timedelta(minutes=15 * i)
        bars.append(
            Candle(
                time_utc=t,
                open=75000.0,
                high=75050.0,
                low=74950.0,
                close=75000.0 + (0.5 if i % 2 == 0 else -0.5),
                volume=100.0,
            )
        )
    # Nến 41 bùng nổ mạnh tăng vọt lên 75500 để bung Squeeze (BB expands > KC)
    bars.append(
        Candle(
            time_utc=now - timedelta(minutes=15),
            open=75000.0,
            high=75500.0,
            low=74990.0,
            close=75450.0,
            volume=300.0,
        )
    )

    snap = MarketSnapshot(
        symbol="BTCUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=75440.0,
        ask=75448.0,
        timeframes={"M15": TimeframeSnapshot("M15", tuple(bars))},
    )

    res = btc_squeeze_breakout_m15.evaluate(snap, cfg, now)
    assert res.candidate is not None
    assert res.candidate.side == "BUY"
    assert res.candidate.stop_loss < res.candidate.reference_entry
    assert res.candidate.take_profit > res.candidate.reference_entry
    assert "BTC_SQUEEZE_BREAKOUT_CONFIRMED" in res.evidence.reason_codes


