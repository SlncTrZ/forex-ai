from datetime import datetime, timedelta, timezone

from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot
from forex_ai.strategy.v1.exploration import evaluate_breakout, evaluate_trend
from forex_ai.strategy.v1.trend_pullback import DEFAULT_CONFIG as TREND_CONFIG, evaluate as evaluate_production_trend
from forex_ai.strategy.v1.volatility_breakout import DEFAULT_CONFIG as BREAKOUT_CONFIG, evaluate as evaluate_production_breakout

UTC = timezone.utc


def _trend_bars(start: datetime, count: int, step: float = 0.1, base: float = 100.0):
    bars = []
    price = base
    for i in range(count):
        opened = price
        closed = price + step
        high = max(opened, closed) + 0.08
        low = min(opened, closed) - 0.08
        bars.append(Candle(start + timedelta(minutes=i), opened, high, low, closed, 100 + i))
        price = closed
    return bars


def _pullback_snapshot(*, h1_mixed: bool = False):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    h4 = _trend_bars(start, 60, 0.25, 100)
    h1 = _trend_bars(start, 60, 0.0 if h1_mixed else 0.18, 100)
    m15 = _trend_bars(start, 60, 0.12, 100)
    prev = m15[-2]
    m15[-2] = Candle(prev.time_utc, prev.open, prev.high, prev.low - 1.5, prev.close, prev.volume)
    latest = m15[-1]
    m15[-1] = Candle(latest.time_utc, latest.open, latest.high + 0.2, latest.low, latest.close + 0.15, latest.volume)
    now = start + timedelta(hours=20)
    return MarketSnapshot(
        "TEST",
        now,
        1234567890000,
        108.0,
        108.02,
        {
            "H4": TimeframeSnapshot.from_sequence("H4", h4),
            "H1": TimeframeSnapshot.from_sequence("H1", h1),
            "M15": TimeframeSnapshot.from_sequence("M15", m15),
        },
        spread_cost=0.01,
    )


def _breakout_snapshot(*, spread_cost: float):
    now = datetime(2026, 1, 2, tzinfo=UTC)
    start = now - timedelta(hours=20)
    bars = _trend_bars(start, 60, 0.04, 100)
    prior_high = max(bar.high for bar in bars[-20:])
    price = bars[-1].close
    bars.append(Candle(now - timedelta(minutes=15), price, prior_high + 0.30, price - 0.05, prior_high + 0.20, 1000))
    return MarketSnapshot(
        "TEST",
        now,
        2,
        prior_high + 0.19,
        prior_high + 0.20,
        {"M15": TimeframeSnapshot.from_sequence("M15", bars)},
        spread_cost=spread_cost,
    )


def test_trend_exploration_keeps_h4_led_h1_mixed_setup_as_tier_b():
    snapshot = _pullback_snapshot(h1_mixed=True)
    production = evaluate_production_trend(snapshot, TREND_CONFIG, snapshot.captured_at_utc)
    assert production.candidate is None
    assert "REGIME_NOT_ALIGNED" in production.no_setup_reason_codes

    exploration = evaluate_trend(snapshot, now_utc=snapshot.captured_at_utc)
    assert exploration.candidate is not None
    assert exploration.evidence.values["tier"] == "B"
    assert exploration.evidence.values["thesis_source"] == "H4_LED"
    assert "REGIME_NOT_ALIGNED" in exploration.evidence.values["failed_original_gates"]


def test_breakout_exploration_softens_cost_gate_but_records_failure():
    snapshot = _breakout_snapshot(spread_cost=10.0)
    production = evaluate_production_breakout(snapshot, BREAKOUT_CONFIG, snapshot.captured_at_utc)
    assert production.candidate is None
    assert "COST_TOO_HIGH" in production.no_setup_reason_codes

    exploration = evaluate_breakout(snapshot, now_utc=snapshot.captured_at_utc)
    assert exploration.candidate is not None
    assert exploration.evidence.values["tier"] in {"B", "C"}
    assert "COST_TOO_HIGH" in exploration.evidence.values["failed_original_gates"]


def test_breakout_exploration_still_requires_real_range_break():
    now = datetime(2026, 1, 2, tzinfo=UTC)
    bars = _trend_bars(now - timedelta(hours=20), 60, 0.02, 100)
    latest = bars[-1]
    bars[-1] = Candle(latest.time_utc, latest.open, latest.high, latest.low, latest.open, latest.volume)
    snapshot = MarketSnapshot(
        "TEST",
        now,
        3,
        bars[-1].close - 0.01,
        bars[-1].close,
        {"M15": TimeframeSnapshot.from_sequence("M15", bars)},
        spread_cost=0.001,
    )
    result = evaluate_breakout(snapshot, now_utc=now)
    assert result.candidate is None
    assert "NO_RANGE_BREAK" in result.no_setup_reason_codes
