"""Macro News Guard & Surprise Index — Event blackout enforcement and post-news momentum detection.
Wing: code | Topic: news_guard | Updated: 2026-10-28 12:15
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from forex_ai.macro.models import EventBlackout, FreshnessPolicy, ScheduledEvent, build_blackouts
from forex_ai.strategy.v1.contracts import Candle

UTC = timezone.utc


@dataclass(frozen=True)
class SurpriseAnalysis:
    event_name: str
    actual: float
    forecast: float
    previous: float
    z_score: float
    classification: str  # "HAWKISH_STRONG", "HAWKISH_MILD", "NEUTRAL", "DOVISH_MILD", "DOVISH_STRONG"
    currency_impact: str  # "BULLISH", "BEARISH", "NEUTRAL"


@dataclass(frozen=True)
class PostNewsSetup:
    valid: bool
    side: str  # "BUY" | "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str


class NewsBlackoutGuard:
    """Bảo vệ tài khoản trong các khung giờ công bố tin tức kinh tế quan trọng."""

    def __init__(self, pre_news_seconds: int = 900, post_news_seconds: int = 900):
        self.policy = FreshnessPolicy(
            ttl_seconds=3600,
            event_pre_seconds=pre_news_seconds,
            event_post_seconds=post_news_seconds,
        )

    def is_in_blackout(self, now_utc: datetime, events: Sequence[ScheduledEvent]) -> tuple[bool, Optional[EventBlackout]]:
        t = now_utc.astimezone(UTC)
        blackouts = build_blackouts(tuple(events), self.policy)
        for b in blackouts:
            if b.contains(t):
                return True, b
        return False, None

    def evaluate_open_position_risk(
        self,
        now_utc: datetime,
        events: Sequence[ScheduledEvent],
        current_unrealized_r: float,
    ) -> str:
        """Đưa ra hành động phòng ngừa rủi ro cho lệnh đang mở trước giờ tin:
        - Nếu đang lãi lớn (>= 1.5R): Siết SL về hòa vốn (MOVE_SL_TO_BREAKEVEN).
        - Nếu đang hòa hoặc âm nhẹ (-0.5R đến +0.5R) và sắp có tin trong 10 phút: Đóng lệnh (CLOSE_BEFORE_NEWS).
        - Các trường hợp khác: Giữ lệnh theo kỷ luật (HOLD_NORMAL).
        """
        t = now_utc.astimezone(UTC)
        for event in events:
            if event.importance.upper() in {"HIGH", "CRITICAL"}:
                time_to_event = (event.starts_at_utc - t).total_seconds()
                # Trong vòng 10 phút trước tin
                if 0 < time_to_event <= 600:
                    if current_unrealized_r >= 1.5:
                        return "MOVE_SL_TO_BREAKEVEN"
                    elif -0.5 <= current_unrealized_r < 0.5:
                        return "CLOSE_BEFORE_NEWS"
        return "HOLD_NORMAL"


class SurpriseIndexCalculator:
    """Đo lường độ chệch vĩ mô (Macro Surprise Factor) giữa số liệu thực tế và kỳ vọng đồng thuận."""

    @staticmethod
    def calculate(
        event_name: str,
        actual: float,
        forecast: float,
        previous: float,
        historical_std: Optional[float] = None,
    ) -> SurpriseAnalysis:
        std = historical_std if (historical_std is not None and historical_std > 0) else max(abs(forecast) * 0.1, 0.001)
        z_score = (actual - forecast) / std

        if z_score >= 1.5:
            classification = "HAWKISH_STRONG"
            impact = "BULLISH"
        elif z_score >= 0.5:
            classification = "HAWKISH_MILD"
            impact = "BULLISH"
        elif z_score <= -1.5:
            classification = "DOVISH_STRONG"
            impact = "BEARISH"
        elif z_score <= -0.5:
            classification = "DOVISH_MILD"
            impact = "BEARISH"
        else:
            classification = "NEUTRAL"
            impact = "NEUTRAL"

        return SurpriseAnalysis(
            event_name=event_name,
            actual=actual,
            forecast=forecast,
            previous=previous,
            z_score=z_score,
            classification=classification,
            currency_impact=impact,
        )


class PostNewsMomentumDetector:
    """Xác nhận tín hiệu bùng nổ theo đà (Post-News Drift) sau khi nến tin tức đóng."""

    @staticmethod
    def evaluate(
        post_news_bar: Candle,
        atr_value: float,
        surprise: SurpriseAnalysis,
        current_spread: float,
        base_spread: float,
    ) -> PostNewsSetup:
        # 1. Spread phải hồi phục về mức an toàn (không quá 1.8x base)
        if current_spread > 1.8 * base_spread:
            return PostNewsSetup(valid=False, side="NONE", entry_price=0, stop_loss=0, take_profit=0, reason="SPREAD_STILL_EXPANDED")

        # 2. Surprise Factor phải đủ lớn (|Z| >= 1.0)
        if abs(surprise.z_score) < 1.0:
            return PostNewsSetup(valid=False, side="NONE", entry_price=0, stop_loss=0, take_profit=0, reason="INSIGNIFICANT_SURPRISE")

        bar_range = post_news_bar.high - post_news_bar.low
        body = abs(post_news_bar.close - post_news_bar.open)
        body_ratio = body / max(bar_range, 1e-6)

        # 3. Nến đóng cửa phải thể hiện Institutional Conviction (body_ratio >= 0.65, range >= 1.2 ATR)
        if body_ratio < 0.65 or bar_range < 1.2 * atr_value:
            return PostNewsSetup(valid=False, side="NONE", entry_price=0, stop_loss=0, take_profit=0, reason="WEAK_CANDLE_CONVICTION")

        # 4. Hướng nến phải đồng thuận với hướng tin vĩ mô
        if surprise.currency_impact == "BULLISH" and post_news_bar.close > post_news_bar.open:
            entry = post_news_bar.close
            sl = post_news_bar.low
            tp = entry + 2.0 * (entry - sl)
            return PostNewsSetup(valid=True, side="BUY", entry_price=entry, stop_loss=sl, take_profit=tp, reason="CONFIRMED_POST_NEWS_BULLISH")
        elif surprise.currency_impact == "BEARISH" and post_news_bar.close < post_news_bar.open:
            entry = post_news_bar.close
            sl = post_news_bar.high
            tp = entry - 2.0 * (sl - entry)
            return PostNewsSetup(valid=True, side="SELL", entry_price=entry, stop_loss=sl, take_profit=tp, reason="CONFIRMED_POST_NEWS_BEARISH")

        return PostNewsSetup(valid=False, side="NONE", entry_price=0, stop_loss=0, take_profit=0, reason="DIRECTION_DISCORDANCE")
