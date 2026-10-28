"""Algorithmic Elliott Wave Roadmap — Causal ATR ZigZag, 3 Invariants Validator, and DTW Motif Matching.
Wing: code | Topic: elliott_roadmap | Updated: 2026-10-28 12:18
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

from forex_ai.strategy.v1.contracts import Candle


@dataclass(frozen=True)
class ElliottWaveCount:
    valid: bool
    direction: str  # "BULLISH" | "BEARISH"
    p0: float
    p1: float
    p2: float
    p3: float
    p4: float
    p5: float
    wave3_ratio: float  # len(W3) / len(W1)
    wave4_retrace: float  # len(W4) / len(W3)
    invalidation_level: float
    macro_phase: str  # "WAVE_3_EXPANSION", "WAVE_5_EXHAUSTION", "INVALID"


@dataclass(frozen=True)
class MacroRoadmap:
    status: str
    phase: str
    bias: str  # "BULLISH_TREND", "BEARISH_TREND", "REVERSAL_WARNING", "NEUTRAL"
    dtw_similarity: float
    invalidation_price: float
    description: str


class DynamicATRZigZag:
    """Trích xuất Pivot nhân quả (Causal) dựa trên độ lệch k * ATR để loại bỏ nhiễu micro."""

    @staticmethod
    def extract(bars: Sequence[Candle], atrs: Sequence[float], k_multiplier: float = 2.0) -> list[tuple[int, float, str]]:
        """Trả về list of (bar_index, price, 'HIGH'|'LOW')"""
        if len(bars) < 5:
            return []

        pivots: list[tuple[int, float, str]] = []
        last_pivot_type: Optional[str] = None
        last_pivot_price: float = bars[0].close
        last_pivot_idx: int = 0

        for i, bar in enumerate(bars):
            atr_val = atrs[i] if i < len(atrs) and atrs[i] > 0 else (bar.high - bar.low)
            threshold = k_multiplier * max(atr_val, 0.0001)

            if last_pivot_type is None:
                if bar.high >= last_pivot_price + threshold:
                    last_pivot_type = "HIGH"
                    last_pivot_price = bar.high
                    last_pivot_idx = i
                    pivots.append((i, bar.high, "HIGH"))
                elif bar.low <= last_pivot_price - threshold:
                    last_pivot_type = "LOW"
                    last_pivot_price = bar.low
                    last_pivot_idx = i
                    pivots.append((i, bar.low, "LOW"))
            elif last_pivot_type == "LOW":
                if bar.high >= last_pivot_price + threshold:
                    last_pivot_type = "HIGH"
                    last_pivot_price = bar.high
                    last_pivot_idx = i
                    pivots.append((i, bar.high, "HIGH"))
                elif bar.low < last_pivot_price:
                    # Update lower low
                    last_pivot_price = bar.low
                    last_pivot_idx = i
                    pivots[-1] = (i, bar.low, "LOW")
            elif last_pivot_type == "HIGH":
                if bar.low <= last_pivot_price - threshold:
                    last_pivot_type = "LOW"
                    last_pivot_price = bar.low
                    last_pivot_idx = i
                    pivots.append((i, bar.low, "LOW"))
                elif bar.high > last_pivot_price:
                    # Update higher high
                    last_pivot_price = bar.high
                    last_pivot_idx = i
                    pivots[-1] = (i, bar.high, "HIGH")

        return pivots


class ElliottInvariantsValidator:
    """Kiểm tra nghiêm ngặt 3 Bất biến của Sóng đẩy (Motive Wave)."""

    @staticmethod
    def validate_bullish_impulse(p0: float, p1: float, p2: float, p3: float, p4: float, p5: float) -> ElliottWaveCount:
        len_w1 = p1 - p0
        len_w3 = p3 - p2
        len_w5 = p5 - p4

        # Điều kiện cơ bản tăng: P1 > P0, P3 > P2, P5 > P4
        if len_w1 <= 0 or len_w3 <= 0 or len_w5 <= 0:
            return ElliottWaveCount(False, "BULLISH", p0, p1, p2, p3, p4, p5, 0, 0, p0, "INVALID")

        # Bất biến 1: Sóng 2 không thoái lui quá điểm xuất phát Sóng 1
        if p2 <= p0:
            return ElliottWaveCount(False, "BULLISH", p0, p1, p2, p3, p4, p5, 0, 0, p0, "INVALID")

        # Bất biến 2: Sóng 3 không bao giờ là sóng ngắn nhất
        if len_w3 < min(len_w1, len_w5):
            return ElliottWaveCount(False, "BULLISH", p0, p1, p2, p3, p4, p5, 0, 0, p0, "INVALID")

        # Bất biến 3: Sóng 4 không overlap vào vùng Sóng 1
        if p4 <= p1:
            return ElliottWaveCount(False, "BULLISH", p0, p1, p2, p3, p4, p5, 0, 0, p1, "INVALID")

        w3_ratio = len_w3 / max(len_w1, 1e-6)
        w4_retrace = (p3 - p4) / max(len_w3, 1e-6)

        phase = "WAVE_5_EXHAUSTION" if p5 >= p3 else "WAVE_3_EXPANSION"
        invalidation = p4  # Thủng đáy sóng 4 là gãy nhãn sóng đẩy

        return ElliottWaveCount(
            valid=True,
            direction="BULLISH",
            p0=p0,
            p1=p1,
            p2=p2,
            p3=p3,
            p4=p4,
            p5=p5,
            wave3_ratio=w3_ratio,
            wave4_retrace=w4_retrace,
            invalidation_level=invalidation,
            macro_phase=phase,
        )


class DynamicTimeWarpingMatcher:
    """So khớp hình học chuỗi thời gian phi tuyến (DTW) giữa giá thị trường và Motif sóng đẩy mẫu."""

    # Mẫu sóng đẩy tăng lý tưởng chuẩn hóa về miền [0, 1]
    IDEAL_BULLISH_MOTIF = [0.0, 0.15, 0.35, 0.22, 0.50, 0.80, 0.65, 0.85, 1.0]

    @classmethod
    def compute_similarity(cls, series: Sequence[float]) -> float:
        if len(series) < 5:
            return 0.0

        min_v = min(series)
        max_v = max(series)
        if max_v <= min_v:
            return 0.0

        # Min-Max normalize
        norm_series = [(x - min_v) / (max_v - min_v) for x in series]
        template = cls.IDEAL_BULLISH_MOTIF

        n = len(norm_series)
        m = len(template)

        # Bảng quy hoạch động DTW
        dtw_matrix = [[float("inf")] * (m + 1) for _ in range(n + 1)]
        dtw_matrix[0][0] = 0.0

        for i in range(1, n + 1):
            for j in range(1, m + 1):
                cost = abs(norm_series[i - 1] - template[j - 1])
                dtw_matrix[i][j] = cost + min(
                    dtw_matrix[i - 1][j],      # Insertion
                    dtw_matrix[i][j - 1],      # Deletion
                    dtw_matrix[i - 1][j - 1],  # Match
                )

        distance = dtw_matrix[n][m] / (n + m)
        # Similarity score in [0.0, 1.0]
        similarity = math.exp(-distance * 3.0)
        return round(similarity, 4)


class MacroRoadmapAnalyzer:
    """Tổng hợp bối cảnh sóng Elliott khung H4/D1 để làm kim chỉ nam chiến lược."""

    def __init__(self, k_atr: float = 2.0):
        self.k_atr = k_atr

    def analyze(self, bars: Sequence[Candle], atrs: Sequence[float]) -> MacroRoadmap:
        if len(bars) < 20:
            return MacroRoadmap("UNAVAILABLE", "NONE", "NEUTRAL", 0.0, 0.0, "Dữ liệu không đủ để đếm sóng")

        pivots = DynamicATRZigZag.extract(bars, atrs, self.k_atr)
        if len(pivots) < 6:
            return MacroRoadmap("FORMING", "INDETERMINATE", "NEUTRAL", 0.0, 0.0, "Đang tích lũy các điểm xoay chiều")

        # Lấy 6 điểm xoay gần nhất để kiểm tra 5 sóng: P0, P1, P2, P3, P4, P5
        last_6 = pivots[-6:]
        # Kiểm tra xem có xen kẽ Low - High - Low - High - Low - High không
        types = [p[2] for p in last_6]
        prices = [p[1] for p in last_6]

        if types == ["LOW", "HIGH", "LOW", "HIGH", "LOW", "HIGH"]:
            count = ElliottInvariantsValidator.validate_bullish_impulse(*prices)
            if count.valid:
                recent_close = [b.close for b in bars[last_6[0][0] :]]
                dtw_sim = DynamicTimeWarpingMatcher.compute_similarity(recent_close)

                if count.macro_phase == "WAVE_5_EXHAUSTION":
                    return MacroRoadmap(
                        status="ACTIVE",
                        phase="WAVE_5_EXHAUSTION",
                        bias="REVERSAL_WARNING",
                        dtw_similarity=dtw_sim,
                        invalidation_price=count.invalidation_level,
                        description="Sóng 5 HTF đã hoàn thành. Cảnh báo cạn kiệt đà tăng, thắt chặt trailing stop và cấm Buy fomo.",
                    )
                else:
                    return MacroRoadmap(
                        status="ACTIVE",
                        phase="WAVE_3_EXPANSION",
                        bias="BULLISH_TREND",
                        dtw_similarity=dtw_sim,
                        invalidation_price=count.invalidation_level,
                        description="Sóng 3 mở rộng mạnh mẽ. Ưu tiên tuyệt đối các lệnh Buy pullback trên khung nhỏ.",
                    )

        return MacroRoadmap("RANGING", "CORRECTIVE_OR_COMPLEX", "NEUTRAL", 0.0, 0.0, "Cấu trúc sóng phức tạp hoặc điều chỉnh phẳng")
