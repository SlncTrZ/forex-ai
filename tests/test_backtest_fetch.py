from datetime import datetime, timezone

from backtest.fetch_previous_week import DEFAULT_SYMBOLS, default_range_start, default_week_start, main

UTC = timezone.utc


def test_default_week_start_on_weekend_uses_week_that_just_ended():
    assert default_week_start(datetime(2026, 9, 5, 8, tzinfo=UTC)).isoformat() == "2026-08-31"


def test_default_week_start_midweek_uses_previous_full_week():
    assert default_week_start(datetime(2026, 9, 2, 8, tzinfo=UTC)).isoformat() == "2026-08-24"


def test_backtest_default_universe_remains_standard_research_pair():
    assert DEFAULT_SYMBOLS == ("EURUSD", "XAUUSD")


def test_default_range_start_for_four_completed_weeks():
    assert default_range_start(datetime(2026, 9, 5, 8, tzinfo=UTC), 4).isoformat() == "2026-08-10"
