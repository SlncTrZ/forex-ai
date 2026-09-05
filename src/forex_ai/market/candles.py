from __future__ import annotations

from dataclasses import dataclass

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


def candle_shape(candle: Candle) -> CandleShape:
    range_value = candle.high - candle.low
    body = abs(candle.close - candle.open)
    upper_wick = candle.high - max(candle.open, candle.close)
    lower_wick = min(candle.open, candle.close) - candle.low
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
        )
    if candle.close > candle.open:
        direction = "BULL"
    elif candle.close < candle.open:
        direction = "BEAR"
    else:
        direction = "DOJI"
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
    )


def is_inside_bar(mother: Candle, inside: Candle) -> bool:
    return inside.high < mother.high and inside.low > mother.low
