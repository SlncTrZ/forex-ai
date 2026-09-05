from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forex_ai.research.replay import ReplayEvent
from forex_ai.research.scalping_harness import _ActiveTrade, _update_trade
from forex_ai.research.scalping_strategies import ScalpingSignal
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot

UTC = timezone.utc
T0 = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _signal(side="BUY") -> ScalpingSignal:
    if side == "BUY":
        entry, stop, target = 100.0, 99.0, 101.5
    else:
        entry, stop, target = 100.0, 101.0, 98.5
    return ScalpingSignal(
        signal_id=f"sig-{side}",
        setup_key=f"setup-{side}",
        strategy_id="test_v1",
        strategy_version="1.0.0",
        strategy_config_fingerprint="a" * 64,
        symbol="XAUUSDc",
        side=side,
        decision_timeframe="M5",
        generated_at_utc=T0,
        expires_at_utc=T0 + timedelta(minutes=45),
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        features={},
    )


def _event(minutes: int, *, high: float, low: float, close: float, spread: float = 0.0) -> ReplayEvent:
    clock = T0 + timedelta(minutes=minutes)
    bar = Candle(clock - timedelta(minutes=5), close, high, low, close, 100)
    snapshot = MarketSnapshot(
        symbol="XAUUSDc",
        captured_at_utc=clock,
        market_time_msc=int(clock.timestamp() * 1000),
        bid=close,
        ask=close + spread,
        timeframes={"M5": TimeframeSnapshot("M5", (bar,))},
    )
    return ReplayEvent(clock, snapshot)


def test_target_exit_records_positive_r_and_mfe():
    active = _ActiveTrade(_signal("BUY"), "OOS", marks={15: None, 30: None})
    record = _update_trade(active, _event(5, high=101.6, low=99.8, close=101.2), horizons=(15, 30), intrabar_policy="stop_first")
    assert record is not None
    assert record.exit_reason == "TARGET"
    assert record.realized_r == 1.5
    assert record.mfe_r >= 1.5
    assert record.marks_r["15"] == 1.5


def test_same_bar_stop_and_target_is_conservative_by_default():
    active = _ActiveTrade(_signal("BUY"), "OOS", marks={15: None})
    record = _update_trade(active, _event(5, high=101.6, low=98.8, close=100.2), horizons=(15,), intrabar_policy="stop_first")
    assert record is not None
    assert record.exit_reason == "AMBIGUOUS_STOP_FIRST"
    assert record.realized_r == -1.0


def test_sell_exit_uses_ask_adjusted_bar_extremes():
    active = _ActiveTrade(_signal("SELL"), "OOS", marks={15: None})
    # Bid low reaches 98.45, but with a 0.10 spread ask low is 98.55, so TP 98.50 is not touched.
    record = _update_trade(active, _event(5, high=100.2, low=98.45, close=98.8, spread=0.10), horizons=(15,), intrabar_policy="stop_first")
    assert record is None
    assert active.mfe_r < 1.5


def test_expiry_closes_at_executable_price():
    active = _ActiveTrade(_signal("BUY"), "OOS", marks={15: None, 30: None})
    assert _update_trade(active, _event(15, high=100.4, low=99.7, close=100.2), horizons=(15, 30), intrabar_policy="stop_first") is None
    record = _update_trade(active, _event(45, high=100.5, low=99.8, close=100.3), horizons=(15, 30), intrabar_policy="stop_first")
    assert record is not None
    assert record.exit_reason == "EXPIRY"
    assert record.realized_r == pytest.approx(0.3)
    assert record.marks_r["15"] == pytest.approx(0.2)
