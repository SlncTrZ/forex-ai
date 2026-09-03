from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forex_ai.research.mt5_dataset import build_replay_events_from_mt5_bars

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def rows(step_seconds: int, count: int, *, spread: int = 2):
    out = []
    for i in range(count):
        price = 1.10 + i * 0.00001
        out.append({
            "time": int((START + timedelta(seconds=step_seconds * i)).timestamp()),
            "open": price,
            "high": price + 0.0001,
            "low": price - 0.0001,
            "close": price + 0.00002,
            "tick_volume": 100 + i,
            "spread": spread,
        })
    return out


def test_mt5_bar_builder_excludes_unclosed_higher_timeframe_bars_and_uses_historical_spread():
    events = build_replay_events_from_mt5_bars(
        symbol="EURUSDc",
        point=0.00001,
        m15_rows=rows(900, 260),
        h1_rows=rows(3600, 100),
        h4_rows=rows(14400, 60),
        history_bars=50,
        min_bars=10,
    )
    assert events
    event = events[-1]
    assert event.snapshot.ask - event.snapshot.bid == pytest.approx(0.00002)
    for name, seconds in (("M15", 900), ("H1", 3600), ("H4", 14400)):
        latest = event.snapshot.timeframes[name].closed_bars[-1]
        assert latest.time_utc + timedelta(seconds=seconds) <= event.clock_utc
    assert event.snapshot.metadata["source"] == "mt5_broker_history"


def test_mt5_bar_builder_requires_history_before_emitting_events():
    events = build_replay_events_from_mt5_bars(
        symbol="EURUSDc",
        point=0.00001,
        m15_rows=rows(900, 20),
        h1_rows=rows(3600, 20),
        h4_rows=rows(14400, 5),
        history_bars=20,
        min_bars=10,
    )
    assert events == ()
