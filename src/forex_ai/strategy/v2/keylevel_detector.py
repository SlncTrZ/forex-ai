"""Module tinh toan thuan tuy (Khong can GUI/Chart) phat hien KeyLevel & Trendline tu mang nen OHLC.

Wing: strategy | Topic: market_structure | Updated: 2026-10-30 20:00

Cung cap cac ham tinh toan de Bot Python chay ngam tren server .227:
- detect_swing_points: Tim cac dinh Swing High va day Swing Low bat doi xung (No-repaint).
- get_keylevels: Tra ve muc gia Khang cu (Resistance) va Ho tro (Support) gan nhat.
- get_trendlines: Tinh toan he so goc (slope, intercept) va muc gia cua Trendline tai cay nen hien tai.
- evaluate_keylevel_breakout: Kiem tra dot pha Keylevel hoac Trendline kem bo loc H1 Bias.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import numpy as np


@dataclass(frozen=True)
class SwingPoint:
    index: int
    time_msc: int
    price: float
    is_high: bool


@dataclass(frozen=True)
class Trendline:
    pt1: SwingPoint
    pt2: SwingPoint
    slope: float      # price change per bar
    intercept: float  # price at bar 0
    is_resistance: bool

    def price_at(self, bar_index: int) -> float:
        return self.pt1.price + self.slope * (bar_index - self.pt1.index)


@dataclass(frozen=True)
class MarketStructureLevels:
    resistance: float | None
    support: float | None
    resistance_trendline: Trendline | None
    support_trendline: Trendline | None
    recent_highs: tuple[SwingPoint, ...]
    recent_lows: tuple[SwingPoint, ...]


def detect_swings(
    highs: Sequence[float],
    lows: Sequence[float],
    times_msc: Sequence[int] | None = None,
    left_bars: int = 15,
    right_bars: int = 5,
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """Tim Swing Highs va Swing Lows theo thuat toan bat doi xung (No-repaint).
    
    Yeu cau phai dong du `right_bars` nen thap hon (hoac cao hon) thi moi xac nhan dinh/day.
    """
    n = len(highs)
    swing_highs: list[SwingPoint] = []
    swing_lows: list[SwingPoint] = []
    
    if n < left_bars + right_bars + 1:
        return swing_highs, swing_lows

    h_arr = np.asarray(highs, dtype=np.float64)
    l_arr = np.asarray(lows, dtype=np.float64)

    # Quet tu qua khu den hien tai (bo qua right_bars nen cuoi chua du xac nhan)
    for i in range(left_bars, n - right_bars):
        cand_h = h_arr[i]
        cand_l = l_arr[i]
        
        # Check left
        is_high = np.all(cand_h >= h_arr[i - left_bars : i])
        is_low = np.all(cand_l <= l_arr[i - left_bars : i])
        
        # Check right
        if is_high:
            is_high = np.all(cand_h > h_arr[i + 1 : i + 1 + right_bars])
        if is_low:
            is_low = np.all(cand_l < l_arr[i + 1 : i + 1 + right_bars])
            
        t_val = times_msc[i] if times_msc is not None else i
        if is_high:
            swing_highs.append(SwingPoint(index=i, time_msc=t_val, price=float(cand_h), is_high=True))
        if is_low:
            swing_lows.append(SwingPoint(index=i, time_msc=t_val, price=float(cand_l), is_high=False))

    return swing_highs, swing_lows


def extract_structure(
    highs: Sequence[float],
    lows: Sequence[float],
    times_msc: Sequence[int] | None = None,
    left_bars: int = 15,
    right_bars: int = 5,
) -> MarketStructureLevels:
    """Trich xuat toan bo Keylevel Khang cu, Ho tro va Trendline hien huu."""
    sw_highs, sw_lows = detect_swings(highs, lows, times_msc, left_bars, right_bars)
    
    res = sw_highs[-1].price if sw_highs else None
    sup = sw_lows[-1].price if sw_lows else None
    
    tl_res = None
    if len(sw_highs) >= 2:
        p1, p2 = sw_highs[-2], sw_highs[-1]
        dx = p2.index - p1.index
        if dx > 0:
            slope = (p2.price - p1.price) / dx
            tl_res = Trendline(pt1=p1, pt2=p2, slope=slope, intercept=p1.price, is_resistance=True)
            
    tl_sup = None
    if len(sw_lows) >= 2:
        p1, p2 = sw_lows[-2], sw_lows[-1]
        dx = p2.index - p1.index
        if dx > 0:
            slope = (p2.price - p1.price) / dx
            tl_sup = Trendline(pt1=p1, pt2=p2, slope=slope, intercept=p1.price, is_resistance=False)
            
    return MarketStructureLevels(
        resistance=res,
        support=sup,
        resistance_trendline=tl_res,
        support_trendline=tl_sup,
        recent_highs=tuple(sw_highs[-5:]),
        recent_lows=tuple(sw_lows[-5:]),
    )
