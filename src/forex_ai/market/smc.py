"""Smart Money Concepts (SMC) & Price Action — Algorithmic Order Blocks, FVG, BOS, and Sweeps.
Wing: code | Topic: smc_engine | Updated: 2026-10-28 12:12
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from forex_ai.strategy.v1.contracts import Candle


@dataclass(frozen=True)
class SwingPoint:
    index: int
    price: float
    kind: str  # "HIGH" | "LOW"
    time_utc: datetime


@dataclass(frozen=True)
class FairValueGap:
    index: int
    top: float
    bottom: float
    consequent_encroachment: float  # 50% CE
    side: str  # "BULLISH" | "BEARISH"
    time_utc: datetime
    mitigated: bool = False

    @property
    def gap_size(self) -> float:
        return abs(self.top - self.bottom)


@dataclass(frozen=True)
class OrderBlock:
    index: int
    top: float
    bottom: float
    side: str  # "BULLISH" | "BEARISH"
    time_utc: datetime
    displacement_atr: float
    has_fvg: bool
    mitigated: bool = False


@dataclass(frozen=True)
class StructureBreakEvent:
    event_type: str  # "BOS_BULLISH", "BOS_BEARISH", "LIQUIDITY_SWEEP_HIGH", "LIQUIDITY_SWEEP_LOW"
    swing_price: float
    break_price: float
    close_price: float
    candle_index: int
    time_utc: datetime


def detect_swing_points(
    bars: Sequence[Candle],
    left: int = 3,
    right: int = 3,
) -> list[SwingPoint]:
    """Trích xuất các đỉnh đáy dao động (Fractals) hoàn toàn nhân quả (Causal)."""
    swings: list[SwingPoint] = []
    n = len(bars)
    if n < left + right + 1:
        return swings

    for i in range(left, n - right):
        cur = bars[i]
        # Check High
        is_high = all(cur.high >= bars[j].high for j in range(i - left, i + right + 1) if j != i)
        if is_high:
            swings.append(SwingPoint(index=i, price=cur.high, kind="HIGH", time_utc=cur.time_utc))

        # Check Low
        is_low = all(cur.low <= bars[j].low for j in range(i - left, i + right + 1) if j != i)
        if is_low:
            swings.append(SwingPoint(index=i, price=cur.low, kind="LOW", time_utc=cur.time_utc))

    return swings


def detect_fair_value_gaps(
    bars: Sequence[Candle],
    atrs: Sequence[float],
    min_gap_atr: float = 0.15,
) -> list[FairValueGap]:
    """Nhận diện Fair Value Gap (FVG) mô hình 3 nến:
    - Bullish FVG: Low[i+1] > High[i-1]
    - Bearish FVG: High[i+1] < Low[i-1]
    """
    fvgs: list[FairValueGap] = []
    n = len(bars)
    if n < 3:
        return fvgs

    for i in range(1, n - 1):
        prev = bars[i - 1]
        mid = bars[i]
        nxt = bars[i + 1]
        atr_val = atrs[i] if i < len(atrs) and atrs[i] > 0 else 1.0

        # 1. Bullish FVG
        if nxt.low > prev.high:
            gap = nxt.low - prev.high
            if gap >= min_gap_atr * atr_val:
                ce = (nxt.low + prev.high) / 2.0
                # Kiểm tra nến sau có quét lấp FVG chưa
                mitigated = any(bars[k].low <= prev.high for k in range(i + 2, n))
                fvgs.append(
                    FairValueGap(
                        index=i,
                        top=nxt.low,
                        bottom=prev.high,
                        consequent_encroachment=ce,
                        side="BULLISH",
                        time_utc=mid.time_utc,
                        mitigated=mitigated,
                    )
                )

        # 2. Bearish FVG
        elif nxt.high < prev.low:
            gap = prev.low - nxt.high
            if gap >= min_gap_atr * atr_val:
                ce = (prev.low + nxt.high) / 2.0
                mitigated = any(bars[k].high >= prev.low for k in range(i + 2, n))
                fvgs.append(
                    FairValueGap(
                        index=i,
                        top=prev.low,
                        bottom=nxt.high,
                        consequent_encroachment=ce,
                        side="BEARISH",
                        time_utc=mid.time_utc,
                        mitigated=mitigated,
                    )
                )

    return fvgs


def detect_order_blocks(
    bars: Sequence[Candle],
    atrs: Sequence[float],
    min_displacement_atr: float = 1.2,
) -> list[OrderBlock]:
    """Nhận diện Order Block: nến ngược chiều trước xung lực mạnh phá vỡ cấu trúc có FVG."""
    obs: list[OrderBlock] = []
    n = len(bars)
    if n < 4:
        return obs

    for i in range(n - 3):
        cur = bars[i]
        nxt = bars[i + 1]
        nxt2 = bars[i + 2]
        atr_val = atrs[i] if i < len(atrs) and atrs[i] > 0 else 1.0

        # Bullish OB: nến giảm i, nến i+1 hoặc i+2 tăng cực mạnh tạo Displacement
        if cur.close < cur.open:
            move = max(nxt.close, nxt2.close) - cur.low
            if move >= min_displacement_atr * atr_val:
                has_fvg = nxt2.low > cur.high
                mitigated = any(bars[k].low <= cur.low for k in range(i + 3, n))
                obs.append(
                    OrderBlock(
                        index=i,
                        top=cur.high,
                        bottom=cur.low,
                        side="BULLISH",
                        time_utc=cur.time_utc,
                        displacement_atr=move / atr_val,
                        has_fvg=has_fvg,
                        mitigated=mitigated,
                    )
                )

        # Bearish OB: nến tăng i, nến i+1 hoặc i+2 giảm cực mạnh
        elif cur.close > cur.open:
            move = cur.high - min(nxt.close, nxt2.close)
            if move >= min_displacement_atr * atr_val:
                has_fvg = nxt2.high < cur.low
                mitigated = any(bars[k].high >= cur.high for k in range(i + 3, n))
                obs.append(
                    OrderBlock(
                        index=i,
                        top=cur.high,
                        bottom=cur.low,
                        side="BEARISH",
                        time_utc=cur.time_utc,
                        displacement_atr=move / atr_val,
                        has_fvg=has_fvg,
                        mitigated=mitigated,
                    )
                )

    return obs


def classify_structure_interaction(
    swing: SwingPoint,
    candle: Candle,
    index: int,
) -> Optional[StructureBreakEvent]:
    """Phân loại tương tác giữa nến và Swing Point:
    - Nếu nến ĐÓNG CỬA vượt qua swing -> BOS hợp lệ.
    - Nếu chỉ có RÂU NẾN quét qua rồi đóng bên trong -> LIQUIDITY SWEEP (Bẫy thanh khoản).
    """
    if swing.kind == "HIGH":
        if candle.close > swing.price:
            return StructureBreakEvent(
                event_type="BOS_BULLISH",
                swing_price=swing.price,
                break_price=candle.high,
                close_price=candle.close,
                candle_index=index,
                time_utc=candle.time_utc,
            )
        elif candle.high > swing.price and candle.close <= swing.price:
            return StructureBreakEvent(
                event_type="LIQUIDITY_SWEEP_HIGH",
                swing_price=swing.price,
                break_price=candle.high,
                close_price=candle.close,
                candle_index=index,
                time_utc=candle.time_utc,
            )
    else:  # LOW
        if candle.close < swing.price:
            return StructureBreakEvent(
                event_type="BOS_BEARISH",
                swing_price=swing.price,
                break_price=candle.low,
                close_price=candle.close,
                candle_index=index,
                time_utc=candle.time_utc,
            )
        elif candle.low < swing.price and candle.close >= swing.price:
            return StructureBreakEvent(
                event_type="LIQUIDITY_SWEEP_LOW",
                swing_price=swing.price,
                break_price=candle.low,
                close_price=candle.close,
                candle_index=index,
                time_utc=candle.time_utc,
            )

    return None
