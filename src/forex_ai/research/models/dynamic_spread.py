"""Dynamic Spread Model — Diurnal session, rollover spike, and volatility spread expansion.
Wing: code | Topic: dynamic_spread | Updated: 2026-10-28 11:58
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc


@dataclass(frozen=True)
class SpreadProfile:
    base_spread: float
    rollover_multiplier: float
    news_multiplier: float
    volatility_sensitivity: float = 0.5


DEFAULT_SPREAD_PROFILES: dict[str, SpreadProfile] = {
    "EURUSD": SpreadProfile(base_spread=0.00015, rollover_multiplier=12.0, news_multiplier=4.0),
    "EURUSDc": SpreadProfile(base_spread=0.00015, rollover_multiplier=12.0, news_multiplier=4.0),
    "XAUUSD": SpreadProfile(base_spread=0.18, rollover_multiplier=8.0, news_multiplier=5.0),
    "XAUUSDc": SpreadProfile(base_spread=0.18, rollover_multiplier=8.0, news_multiplier=5.0),
}


class DynamicSpreadModel:
    """Mô hình hóa spread động theo thời gian thực thi:
    - Phiên giao dịch bình thường: spread cơ sở kèm dao động vi mô.
    - Giờ Rollover (21:45 - 23:15 UTC): giãn nở đột biến do quyết toán ngân hàng.
    - Tin tức vĩ mô (High impact): giãn nở do hố đen thanh khoản (liquidity vacuum).
    - Biến động tức thời (ATR Expansion): giãn nở theo tỷ lệ biến động.
    """

    def __init__(self, profiles: dict[str, SpreadProfile] | None = None):
        self.profiles = profiles or DEFAULT_SPREAD_PROFILES

    def calculate_spread(
        self,
        symbol: str,
        time_utc: datetime,
        *,
        atr_ratio: float = 1.0,
        is_news_window: bool = False,
    ) -> float:
        profile = self.profiles.get(
            symbol,
            SpreadProfile(base_spread=0.0002, rollover_multiplier=10.0, news_multiplier=4.0),
        )

        t_utc = time_utc.astimezone(UTC)
        hour_fraction = t_utc.hour + t_utc.minute / 60.0 + t_utc.second / 3600.0

        # Rollover Gaussian curve centered at 22:00 UTC (sigma = 0.45 hour ~ 27 mins)
        # Bắt đầu dâng từ 21:15, đạt đỉnh 22:00, hạ nhiệt về 23:15 UTC
        dist = hour_fraction - 22.0
        session_mult = 1.0 + (profile.rollover_multiplier - 1.0) * math.exp(-(dist**2) / (2 * (0.45**2)))

        # Volatility multiplier: ATR(1) / ATR(20) ratio
        vol_mult = 1.0 + profile.volatility_sensitivity * max(0.0, atr_ratio - 1.0)

        # News window multiplier
        news_mult = profile.news_multiplier if is_news_window else 1.0

        total_spread = profile.base_spread * session_mult * vol_mult * news_mult
        return max(profile.base_spread, total_spread)
