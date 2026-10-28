"""Tests for Quantitative Candlestick Formulations and Smart Money Concepts (SMC).
Wing: code | Topic: test_candles_and_smc | Updated: 2026-10-28 12:35
"""

from __future__ import annotations

from datetime import datetime, timezone

from forex_ai.market.candles import (
    candle_shape,
    is_bearish_engulfing,
    is_bearish_pinbar,
    is_bullish_engulfing,
    is_bullish_pinbar,
    is_inside_bar_fakeout,
    is_morning_star,
)
from forex_ai.market.smc import (
    classify_structure_interaction,
    detect_fair_value_gaps,
    detect_order_blocks,
    detect_swing_points,
    SwingPoint,
)
from forex_ai.strategy.v1.contracts import Candle

UTC = timezone.utc


def _c(t_min: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        time_utc=datetime(2026, 10, 28, 10, t_min, tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def test_quantitative_candles_pinbar_and_engulfing():
    # Bullish Pinbar: Râu dưới dài, thân nhỏ ở đỉnh
    pin = _c(0, 1.1000, 1.1002, 1.0950, 1.1001)
    assert is_bullish_pinbar(pin, atr_value=0.0050)
    assert not is_bearish_pinbar(pin, atr_value=0.0050)

    # Bearish Pinbar: Râu trên dài, thân nhỏ ở đáy
    bear_pin = _c(5, 1.1000, 1.1050, 1.0998, 1.0999)
    assert is_bearish_pinbar(bear_pin, atr_value=0.0050)

    # Bullish Engulfing
    c1 = _c(10, 1.1020, 1.1025, 1.1000, 1.1005)  # Nến giảm
    c2 = _c(15, 1.1000, 1.1035, 1.0995, 1.1030)  # Nến tăng nuốt trọn
    assert is_bullish_engulfing(c1, c2, atr_value=0.0030)
    assert not is_bearish_engulfing(c1, c2, atr_value=0.0030)


def test_inside_bar_fakeout_sweep():
    mother = _c(0, 2700.0, 2720.0, 2690.0, 2710.0)
    inside = _c(5, 2705.0, 2715.0, 2695.0, 2700.0)
    # Trigger quét đáy mother (2690) xuống 2685 rồi rút chân đóng 2715
    trigger = _c(10, 2695.0, 2716.0, 2685.0, 2715.0)

    valid, sweep_type = is_inside_bar_fakeout(mother, inside, trigger)
    assert valid
    assert sweep_type == "BULLISH_SWEEP"


def test_smc_fair_value_gap_detection():
    # Bullish FVG: Low[2] > High[0]
    b0 = _c(0, 2700.0, 2705.0, 2698.0, 2702.0)
    b1 = _c(5, 2703.0, 2720.0, 2702.0, 2718.0)  # Xung lực tăng mạnh
    b2 = _c(10, 2718.0, 2725.0, 2710.0, 2722.0)  # Low = 2710 > High b0 = 2705

    fvgs = detect_fair_value_gaps([b0, b1, b2], atrs=[5.0, 5.0, 5.0], min_gap_atr=0.2)
    assert len(fvgs) == 1
    assert fvgs[0].side == "BULLISH"
    assert fvgs[0].bottom == 2705.0
    assert fvgs[0].top == 2710.0
    assert fvgs[0].consequent_encroachment == 2707.5


def test_smc_order_block_detection():
    # Bullish OB: nến giảm b0, sau đó b1 và b2 tăng mạnh tạo FVG
    b0 = _c(0, 2705.0, 2706.0, 2695.0, 2698.0)  # Down candle
    b1 = _c(5, 2698.0, 2720.0, 2697.0, 2718.0)  # Strong impulse
    b2 = _c(10, 2718.0, 2730.0, 2710.0, 2728.0)
    b3 = _c(15, 2728.0, 2732.0, 2725.0, 2730.0)

    obs = detect_order_blocks([b0, b1, b2, b3], atrs=[5.0, 5.0, 5.0, 5.0], min_displacement_atr=1.5)
    assert len(obs) >= 1
    assert obs[0].side == "BULLISH"
    assert obs[0].bottom == 2695.0
    assert obs[0].top == 2706.0
    assert obs[0].has_fvg


def test_smc_bos_vs_liquidity_sweep_invariant():
    swing = SwingPoint(index=5, price=2750.0, kind="HIGH", time_utc=datetime(2026, 10, 28, 10, 0, tzinfo=UTC))

    # Case 1: Thân nến đóng vượt đỉnh cũ -> BOS xác nhận
    bar_bos = _c(10, 2745.0, 2755.0, 2742.0, 2752.0)
    res_bos = classify_structure_interaction(swing, bar_bos, 10)
    assert res_bos is not None
    assert res_bos.event_type == "BOS_BULLISH"

    # Case 2: Chỉ có râu nến chọc qua rồi rút chân đóng dưới -> Liquidity Sweep (Stop Hunt)
    bar_sweep = _c(15, 2745.0, 2755.0, 2742.0, 2748.0)
    res_sweep = classify_structure_interaction(swing, bar_sweep, 15)
    assert res_sweep is not None
    assert res_sweep.event_type == "LIQUIDITY_SWEEP_HIGH"
