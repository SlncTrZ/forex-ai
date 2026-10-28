"""Slippage & Market Impact Model — Almgren-Chriss square-root law and asymmetric adverse slippage.
Wing: code | Topic: slippage_impact | Updated: 2026-10-28 12:00
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SlippageProfile:
    base_slippage: float
    impact_coefficient: float = 0.5  # Hằng số Y trong luật căn bậc hai
    adverse_probability: float = 0.75  # 75% lệnh Market/Stop chịu trượt âm
    laplace_scale: float = 0.5  # Phân phối đuôi dày


DEFAULT_SLIPPAGE_PROFILES: dict[str, SlippageProfile] = {
    "EURUSD": SlippageProfile(base_slippage=0.00005, laplace_scale=0.00003),
    "EURUSDc": SlippageProfile(base_slippage=0.00005, laplace_scale=0.00003),
    "XAUUSD": SlippageProfile(base_slippage=0.08, laplace_scale=0.05),
    "XAUUSDc": SlippageProfile(base_slippage=0.08, laplace_scale=0.05),
}


class SlippageModel:
    """Mô hình trượt giá phi đối xứng Almgren-Chriss:
    - Lệnh Take Profit (Limit Order): Khớp chuẩn giá hoặc có cơ hội trượt dương nhỏ.
    - Lệnh Stop Loss / Market Breakout (Stop Market Order): Trượt giá âm bất đối xứng do Adverse Selection.
    """

    def __init__(
        self,
        profiles: dict[str, SlippageProfile] | None = None,
        seed: int | None = 42,
    ):
        self.profiles = profiles or DEFAULT_SLIPPAGE_PROFILES
        self.rng = random.Random(seed)

    def calculate_slippage(
        self,
        symbol: str,
        order_type: str,  # "MARKET", "LIMIT", "STOP"
        volume: float,
        *,
        volatility_atr: float = 0.0,
        market_volume: float = 1000.0,
    ) -> float:
        profile = self.profiles.get(
            symbol,
            SlippageProfile(base_slippage=0.0001, laplace_scale=0.00005),
        )

        # 1. Market Impact theo luật căn bậc hai Almgren-Chriss
        ratio = max(volume / max(market_volume, 1.0), 0.0)
        impact = profile.impact_coefficient * volatility_atr * (ratio**0.5)

        # 2. Phân phối trượt giá ngẫu nhiên theo loại lệnh
        if order_type == "LIMIT":
            # Limit order (Take Profit): Có thể trượt dương nhẹ hoặc 0
            if self.rng.random() < 0.2:
                positive_bonus = self.rng.expovariate(1.0 / max(profile.laplace_scale, 1e-6)) * 0.3
                return -min(positive_bonus, profile.base_slippage)  # Âm = có lợi cho trader
            return 0.0

        # Lệnh Market / Stop: Trượt giá bất lợi (Adverse Selection)
        adverse_noise = abs(self.rng.expovariate(1.0 / max(profile.laplace_scale, 1e-6)))
        total_adverse = profile.base_slippage + impact + adverse_noise
        return total_adverse  # Dương = giá khớp tệ hơn
