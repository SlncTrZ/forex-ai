"""Tiered Compound Risk Engine — Phân bổ vốn lãi kép và giảm cấp rủi ro theo Level.
Wing: risk | Topic: tiered_compound | Updated: 2026-10-29 17:40

Mô hình định lượng theo tài liệu `_private/lãi kép.xlsx`:
- Vốn khởi tạo V0 luôn là Level 0.
- Tăng trưởng: L(E) = floor(ln(E/V0)/ln(1.1) + 1e-9) khi E >= V0.
- Sụt giảm: L(E) = -ceil(ln(E/V0)/ln(0.9) - 1e-9) khi E < V0.
- Bậc rủi ro:
    * L <= 17: 3.0%
    * 18 <= L <= 50: 2.0%
    * 51 <= L <= 99: 1.0%
    * L >= 100: 0.5%
- Cơ chế Drawdown de-escalation: khi L < -5, duy trì rủi ro 3.0% và tiếp tục
  giao dịch với lot tối thiểu (0.01) cho tới khi cạn kiệt margin.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

D = Decimal


@dataclass(frozen=True)
class TieredRiskEvaluation:
    level: int
    risk_pct: Decimal
    risk_budget: Decimal
    is_drawdown_mode: bool
    status: str
    reason: str


class TieredCompoundRiskEngine:
    def __init__(self, v0: Decimal, hysteresis_pct: Decimal = D("0.015")) -> None:
        if v0 <= 0:
            raise ValueError("Initial deposit V0 must be > 0")
        self.v0 = v0
        self.hysteresis_pct = hysteresis_pct

    def get_threshold(self, level: int) -> Decimal:
        """Tính ngưỡng vốn lý thuyết cho từng level."""
        if level >= 0:
            return self.v0 * (D("1.1") ** level)
        else:
            return self.v0 * (D("0.9") ** abs(level))

    def calculate_level(self, equity: Decimal, previous_level: Optional[int] = None) -> int:
        """Xác định Level dựa trên Equity thực tế, có bộ lọc trễ Hysteresis chống rung lắc."""
        if equity <= 0:
            return -999
        eq_float = float(equity)
        v0_float = float(self.v0)

        if equity >= self.v0:
            raw_lvl = int(math.floor((math.log(eq_float / v0_float) / math.log(1.1)) + 1e-9))
        else:
            k = math.log(eq_float / v0_float) / math.log(0.9)
            raw_lvl = -int(math.ceil(k - 1e-9))

        if previous_level is None:
            return raw_lvl

        # Hysteresis dampening: yêu cầu vượt qua ngưỡng an toàn trước khi đổi level
        if raw_lvl > previous_level:
            target_thresh = self.get_threshold(previous_level + 1)
            if equity < target_thresh:
                return previous_level
            return raw_lvl
        elif raw_lvl < previous_level:
            curr_thresh = self.get_threshold(previous_level)
            lower_bound_with_hysteresis = curr_thresh * (D("1") - self.hysteresis_pct)
            if equity >= lower_bound_with_hysteresis:
                return previous_level
            return raw_lvl
        return raw_lvl

    def get_risk_pct(self, level: int) -> Decimal:
        """Trả về tỷ lệ rủi ro danh nghĩa (nominal risk pct) ứng với Level."""
        if level <= 17:
            return D("0.03")
        elif 18 <= level <= 50:
            return D("0.02")
        elif 51 <= level <= 99:
            return D("0.01")
        else:
            return D("0.005")

    def evaluate_budget(
        self,
        equity: Decimal,
        previous_level: Optional[int] = None,
    ) -> TieredRiskEvaluation:
        """Đánh giá ngân sách rủi ro dựa trên Equity hiện tại."""
        level = self.calculate_level(equity, previous_level)
        risk_pct = self.get_risk_pct(level)
        risk_budget = equity * risk_pct
        is_drawdown = level < 0

        status = "TIERED_OK"
        reason = f"LEVEL_{level}_RISK_{risk_pct * D('100')}%"
        if level < -5:
            status = "DEEP_DRAWDOWN_TRADING"
            reason = f"DEEP_DRAWDOWN_LEVEL_{level}_CONTINUE_MIN_LOT"

        return TieredRiskEvaluation(
            level=level,
            risk_pct=risk_pct,
            risk_budget=risk_budget,
            is_drawdown_mode=is_drawdown,
            status=status,
            reason=reason,
        )
