"""Tests for News Blackout Guard, Surprise Index, and Algorithmic Elliott Wave.
Wing: code | Topic: test_news_and_elliott | Updated: 2026-10-28 12:38
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.macro.models import ScheduledEvent
from forex_ai.macro.news_guard import NewsBlackoutGuard, PostNewsMomentumDetector, SurpriseIndexCalculator
from forex_ai.market.elliott import (
    DynamicATRZigZag,
    DynamicTimeWarpingMatcher,
    ElliottInvariantsValidator,
    MacroRoadmapAnalyzer,
)
from forex_ai.strategy.v1.contracts import Candle

UTC = timezone.utc


def test_news_blackout_guard():
    guard = NewsBlackoutGuard(pre_news_seconds=900, post_news_seconds=900)  # 15 mins before & after
    news_time = datetime(2026, 10, 28, 12, 30, tzinfo=UTC)  # US CPI lúc 12:30 UTC
    event = ScheduledEvent("e1", "US_CPI", news_time, "HIGH")

    # 1. Thời điểm 12:00 (trước tin 30p) -> Chưa blackout
    in_bo, _ = guard.is_in_blackout(datetime(2026, 10, 28, 12, 0, tzinfo=UTC), [event])
    assert not in_bo

    # 2. Thời điểm 12:20 (trước tin 10p) -> Blackout active
    in_bo, bo = guard.is_in_blackout(datetime(2026, 10, 28, 12, 20, tzinfo=UTC), [event])
    assert in_bo
    assert bo is not None

    # 3. Đánh giá rủi ro lệnh mở trước tin 5 phút
    t_check = datetime(2026, 10, 28, 12, 25, tzinfo=UTC)
    # Lệnh đang lãi lớn -> dời SL về hòa vốn
    act1 = guard.evaluate_open_position_risk(t_check, [event], current_unrealized_r=2.1)
    assert act1 == "MOVE_SL_TO_BREAKEVEN"

    # Lệnh đang hòa hoặc âm nhẹ -> đóng lệnh trước tin
    act2 = guard.evaluate_open_position_risk(t_check, [event], current_unrealized_r=0.1)
    assert act2 == "CLOSE_BEFORE_NEWS"


def test_surprise_index_calculator():
    analysis = SurpriseIndexCalculator.calculate(
        event_name="NFP",
        actual=250.0,
        forecast=180.0,
        previous=150.0,
        historical_std=35.0,
    )
    # Z = (250 - 180) / 35 = 2.0 -> Hawkish Strong
    assert analysis.z_score == 2.0
    assert analysis.classification == "HAWKISH_STRONG"
    assert analysis.currency_impact == "BULLISH"


def test_elliott_invariants_validator():
    # Kịch bản sóng 5 hợp lệ: P0=100, P1=120 (len 20), P2=105 (len 15 retrace, P2>P0),
    # P3=145 (len 40), P4=125 (retrace 20, P4>P1), P5=155 (len 30)
    # Wave lengths: W1=20, W3=40, W5=10. W3(40) >= min(20, 10) -> Valid!
    valid_count = ElliottInvariantsValidator.validate_bullish_impulse(100, 120, 105, 145, 125, 155)
    assert valid_count.valid
    assert valid_count.direction == "BULLISH"

    # Vi phạm Bất biến 1: Sóng 2 thủng đáy Sóng 0 (P2 <= P0)
    invalid_w2 = ElliottInvariantsValidator.validate_bullish_impulse(100, 120, 95, 145, 125, 155)
    assert not invalid_w2.valid

    # Vi phạm Bất biến 3: Sóng 4 overlap vào Sóng 1 (P4 <= P1)
    invalid_w4 = ElliottInvariantsValidator.validate_bullish_impulse(100, 120, 105, 145, 115, 155)
    assert not invalid_w4.valid


def test_dtw_matcher_similarity():
    # Một đường tăng sóng đẩy chuẩn
    series = [100.0, 105.0, 115.0, 110.0, 125.0, 140.0, 132.0, 145.0, 155.0]
    sim = DynamicTimeWarpingMatcher.compute_similarity(series)
    assert 0.70 <= sim <= 1.0
