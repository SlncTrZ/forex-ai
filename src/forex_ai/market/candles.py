"""Quantitative Candlestick Formulation — ATR-normalized geometric patterns and sweeps.
Wing: code | Topic: quantitative_candles | Updated: 2026-10-28 12:08
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from forex_ai.strategy.v1.contracts import Candle


@dataclass(frozen=True)
class CandleShape:
    body: float
    range: float
    upper_wick: float
    lower_wick: float
    body_ratio: float
    upper_wick_ratio: float
    lower_wick_ratio: float
    close_position: float
    direction: str
    k_range: float = 1.0
    k_body: float = 0.5


def candle_shape(candle: Candle, atr_value: Optional[float] = None) -> CandleShape:
    range_value = candle.high - candle.low
    body = abs(candle.close - candle.open)
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
    atr_norm = atr_value if (atr_value is not None and atr_value > 0) else range_value

    if range_value <= 0:
        return CandleShape(
            body=body,
            range=0.0,
            upper_wick=0.0,
            lower_wick=0.0,
            body_ratio=0.0,
            upper_wick_ratio=0.0,
            lower_wick_ratio=0.0,
            close_position=0.5,
            direction="DOJI",
            k_range=0.0,
            k_body=0.0,
        )

    if candle.close > candle.open:
        direction = "BULL"
    elif candle.close < candle.open:
        direction = "BEAR"
    else:
        direction = "DOJI"

    k_range = (range_value / atr_norm) if atr_norm > 0 else 1.0
    k_body = (body / atr_norm) if atr_norm > 0 else 0.5

    return CandleShape(
        body=body,
        range=range_value,
        upper_wick=upper_wick,
        lower_wick=lower_wick,
        body_ratio=body / range_value,
        upper_wick_ratio=upper_wick / range_value,
        lower_wick_ratio=lower_wick / range_value,
        close_position=(candle.close - candle.low) / range_value,
        direction=direction,
        k_range=k_range,
        k_body=k_body,
    )


def is_inside_bar(mother: Candle, inside: Candle) -> bool:
    return inside.high < mother.high and inside.low > mother.low


def is_bullish_pinbar(
    candle: Candle,
    atr_value: float,
    *,
    min_k_range: float = 0.8,
    min_lower_wick_ratio: float = 0.60,
    max_upper_wick_ratio: float = 0.15,
) -> bool:
    shape = candle_shape(candle, atr_value)
    if shape.k_range < min_k_range:
        return False
    return (
        shape.lower_wick_ratio >= min_lower_wick_ratio
        and shape.upper_wick_ratio <= max_upper_wick_ratio
        and shape.close_position >= 0.65
    )


def is_bearish_pinbar(
    candle: Candle,
    atr_value: float,
    *,
    min_k_range: float = 0.8,
    min_upper_wick_ratio: float = 0.60,
    max_lower_wick_ratio: float = 0.15,
) -> bool:
    shape = candle_shape(candle, atr_value)
    if shape.k_range < min_k_range:
        return False
    return (
        shape.upper_wick_ratio >= min_upper_wick_ratio
        and shape.lower_wick_ratio <= max_lower_wick_ratio
        and shape.close_position <= 0.35
    )


def is_bullish_engulfing(
    prev_candle: Candle,
    cur_candle: Candle,
    atr_value: float,
    *,
    min_k_range: float = 0.9,
) -> bool:
    prev_shape = candle_shape(prev_candle, atr_value)
    cur_shape = candle_shape(cur_candle, atr_value)
    if cur_shape.k_range < min_k_range or cur_shape.direction != "BULL":
        return False
    # Phải có nến trước là nến giảm hoặc doji
    if prev_shape.direction == "BULL":
        return False
    # Nến hiện tại nuốt trọn thân nến trước
    return (
        cur_candle.close >= prev_candle.open
        and cur_shape.body >= 1.15 * prev_shape.body
    )


def is_bearish_engulfing(
    prev_candle: Candle,
    cur_candle: Candle,
    atr_value: float,
    *,
    min_k_range: float = 0.9,
) -> bool:
    prev_shape = candle_shape(prev_candle, atr_value)
    cur_shape = candle_shape(cur_candle, atr_value)
    if cur_shape.k_range < min_k_range or cur_shape.direction != "BEAR":
        return False
    if prev_shape.direction == "BEAR":
        return False
    return (
        cur_candle.close <= prev_candle.open
        and cur_shape.body >= 1.15 * prev_shape.body
    )


def is_inside_bar_fakeout(mother: Candle, inside: Candle, trigger: Candle) -> tuple[bool, str]:
    """Nhận diện bẫy quét thanh khoản Inside Bar (Hikkake/Sweep):
    - Trả về (True, "BULLISH_SWEEP") nếu trigger quét thủng đáy mother rồi đóng cửa rút chân lên trên.
    - Trả về (True, "BEARISH_SWEEP") nếu trigger quét qua đỉnh mother rồi đóng cửa rút chân xuống dưới.
    """
    if not is_inside_bar(mother, inside):
        return False, "NONE"

    # Bearish Fakeout: Quét đỉnh mẹ rồi sập
    if trigger.high > mother.high and trigger.close < (mother.high + mother.low) / 2.0:
        return True, "BEARISH_SWEEP"

    # Bullish Fakeout: Quét đáy mẹ rồi rút chân tăng
    if trigger.low < mother.low and trigger.close > (mother.high + mother.low) / 2.0:
        return True, "BULLISH_SWEEP"

    return False, "NONE"


def is_morning_star(bar1: Candle, bar2: Candle, bar3: Candle, atr_value: float) -> bool:
    s1 = candle_shape(bar1, atr_value)
    s2 = candle_shape(bar2, atr_value)
    s3 = candle_shape(bar3, atr_value)

    if s1.direction != "BEAR" or s1.k_range < 0.9 or s1.body_ratio < 0.5:
        return False
    if s2.body_ratio > 0.40 or s2.k_range > s1.k_range:
        return False
    if s3.direction != "BULL" or s3.k_range < 0.8:
        return False

    midpoint_bar1 = (bar1.open + bar1.close) / 2.0
    return bar3.close >= midpoint_bar1


def is_evening_star(bar1: Candle, bar2: Candle, bar3: Candle, atr_value: float) -> bool:
    s1 = candle_shape(bar1, atr_value)
    s2 = candle_shape(bar2, atr_value)
    s3 = candle_shape(bar3, atr_value)

    if s1.direction != "BULL" or s1.k_range < 0.9 or s1.body_ratio < 0.5:
        return False
    if s2.body_ratio > 0.40 or s2.k_range > s1.k_range:
        return False
    if s3.direction != "BEAR" or s3.k_range < 0.8:
        return False

    midpoint_bar1 = (bar1.open + bar1.close) / 2.0
    return bar3.close <= midpoint_bar1
