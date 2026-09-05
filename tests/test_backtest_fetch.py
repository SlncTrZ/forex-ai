from datetime import datetime, timezone

from backtest.fetch_previous_week import default_week_start

UTC = timezone.utc


def test_default_week_start_on_weekend_uses_week_that_just_ended():
    assert default_week_start(datetime(2026, 9, 5, 8, tzinfo=UTC)).isoformat() == "2026-08-31"


def test_default_week_start_midweek_uses_previous_full_week():
    assert default_week_start(datetime(2026, 9, 2, 8, tzinfo=UTC)).isoformat() == "2026-08-24"
