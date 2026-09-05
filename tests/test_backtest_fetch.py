from datetime import datetime, timezone

from backtest.fetch_previous_week import DEFAULT_SYMBOLS, default_range_start, default_week_start, main

UTC = timezone.utc


def test_default_week_start_on_weekend_uses_week_that_just_ended():
    assert default_week_start(datetime(2026, 9, 5, 8, tzinfo=UTC)).isoformat() == "2026-08-31"


def test_default_week_start_midweek_uses_previous_full_week():
    assert default_week_start(datetime(2026, 9, 2, 8, tzinfo=UTC)).isoformat() == "2026-08-24"


def test_backtest_default_universe_excludes_retired_symbol():
    assert DEFAULT_SYMBOLS == ("EURUSD", "XAUUSD")
    assert "GBPUSD" not in DEFAULT_SYMBOLS


def test_backtest_cli_rejects_retired_symbol(monkeypatch):
    import pytest, sys
    monkeypatch.setattr(sys, "argv", ["fetch_previous_week.py", "--symbols", "GBPUSD"])
    with pytest.raises(ValueError, match="Unsupported backtest symbols"):
        main()


def test_default_range_start_for_four_completed_weeks():
    assert default_range_start(datetime(2026, 9, 5, 8, tzinfo=UTC), 4).isoformat() == "2026-08-10"
