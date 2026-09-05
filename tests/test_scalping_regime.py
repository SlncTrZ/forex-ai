from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from forex_ai.research.scalping_config import load_scalping_research_config
from forex_ai.research.scalping_regime import audit_signal_regime, classify_regime, signal_alignment
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot

UTC = timezone.utc
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _trend_bars(direction: int, count: int = 80) -> tuple[Candle, ...]:
    rows = []
    price = 100.0
    for index in range(count):
        move = 0.45 * direction
        open_ = price
        close = price + move
        high = max(open_, close) + 0.15
        low = min(open_, close) - 0.15
        rows.append(Candle(NOW - timedelta(minutes=15 * (count - index)), open_, high, low, close, 100 + index))
        price = close
    return tuple(rows)


def _sideways_bars(count: int = 80) -> tuple[Candle, ...]:
    rows = []
    for index in range(count):
        center = 100.0 + (0.25 if index % 2 == 0 else -0.25)
        rows.append(Candle(NOW - timedelta(minutes=15 * (count - index)), center, center + 0.3, center - 0.3, center, 100))
    return tuple(rows)


def _harness():
    return load_scalping_research_config(Path("config/scalping-strategies.yaml")).harness


def test_regime_classifier_is_direction_symmetric():
    harness = _harness()
    up = classify_regime(TimeframeSnapshot("M15", _trend_bars(1)), harness)
    down = classify_regime(TimeframeSnapshot("M15", _trend_bars(-1)), harness)
    assert up.regime == "UP"
    assert down.regime == "DOWN"
    assert up.ema_separation_atr is not None and up.ema_separation_atr > 0
    assert down.ema_separation_atr is not None and down.ema_separation_atr < 0


def test_sideways_is_not_forced_into_up_or_down():
    regime = classify_regime(TimeframeSnapshot("M15", _sideways_bars()), _harness())
    assert regime.regime == "SIDEWAYS"


def test_alignment_normalizes_buy_and_sell_by_regime():
    assert signal_alignment("BUY", "UP") == "WITH_TREND"
    assert signal_alignment("SELL", "DOWN") == "WITH_TREND"
    assert signal_alignment("SELL", "UP") == "COUNTER_TREND"
    assert signal_alignment("BUY", "DOWN") == "COUNTER_TREND"
    assert signal_alignment("BUY", "SIDEWAYS") == "NO_TREND"


def test_regime_is_audit_feature_not_decision_input():
    bars = _trend_bars(1)
    snapshot = MarketSnapshot(
        symbol="XAUUSDc",
        captured_at_utc=NOW,
        market_time_msc=int(NOW.timestamp() * 1000),
        bid=100.0,
        ask=100.1,
        timeframes={"M15": TimeframeSnapshot("M15", bars)},
    )
    buy = audit_signal_regime(snapshot, "BUY", _harness())
    sell = audit_signal_regime(snapshot, "SELL", _harness())
    assert buy["regime"] == sell["regime"] == "UP"
    assert buy["regime_alignment"] == "WITH_TREND"
    assert sell["regime_alignment"] == "COUNTER_TREND"
