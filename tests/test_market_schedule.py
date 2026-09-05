from datetime import datetime, timezone

from forex_ai.runtime.market_schedule import new_entries_allowed, weekend_force_close_due

UTC = timezone.utc


def test_friday_cutoff_uses_new_york_dst_in_summer():
    # 2026-09-04: New York is UTC-4.
    assert new_entries_allowed(datetime(2026, 9, 4, 19, 59, tzinfo=UTC))
    assert not new_entries_allowed(datetime(2026, 9, 4, 20, 0, tzinfo=UTC))
    assert not weekend_force_close_due(datetime(2026, 9, 4, 20, 29, tzinfo=UTC))
    assert weekend_force_close_due(datetime(2026, 9, 4, 20, 30, tzinfo=UTC))


def test_friday_cutoff_uses_new_york_standard_time_in_winter():
    # 2026-12-04: New York is UTC-5.
    assert new_entries_allowed(datetime(2026, 12, 4, 20, 59, tzinfo=UTC))
    assert not new_entries_allowed(datetime(2026, 12, 4, 21, 0, tzinfo=UTC))
    assert not weekend_force_close_due(datetime(2026, 12, 4, 21, 29, tzinfo=UTC))
    assert weekend_force_close_due(datetime(2026, 12, 4, 21, 30, tzinfo=UTC))


def test_weekend_blocks_new_entries_but_does_not_repeat_force_close():
    assert not new_entries_allowed(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))
    assert not new_entries_allowed(datetime(2026, 9, 6, 12, 0, tzinfo=UTC))
    assert not weekend_force_close_due(datetime(2026, 9, 5, 12, 0, tzinfo=UTC))
