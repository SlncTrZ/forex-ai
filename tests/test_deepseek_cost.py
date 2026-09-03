from datetime import datetime, timezone

from forex_ai.intelligence.deepseek import estimate_deepseek_cost, is_peak_utc


def test_peak_window_weekday():
    assert is_peak_utc(datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc))
    assert is_peak_utc(datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc))
    assert not is_peak_utc(datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc))


def test_flash_off_peak_cost():
    cost = estimate_deepseek_cost(
        model="deepseek-v4-flash",
        request_time_utc=datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc),
        cache_hit_tokens=100_000,
        cache_miss_tokens=100_000,
        output_tokens=10_000,
    )
    assert abs(cost - (0.0007 + 0.022 + 0.0066)) < 1e-12
