from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from forex_ai.research.scalping_config import load_scalping_research_config
from forex_ai.research.scalping_strategies import (
    evaluate_breakout_retest,
    evaluate_ema_cross,
    evaluate_inside_bar,
    evaluate_pinbar,
)
from forex_ai.strategy.config import bundled_strategy_snapshot
from forex_ai.strategy.v1.breakout_retest import evaluate as evaluate_breakout_retest_live
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot
from forex_ai.strategy.v1.inside_bar_momentum_breakout import evaluate as evaluate_inside_bar_live

UTC = timezone.utc
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(NOW - timedelta(minutes=5 * (40 - index)), open_, high, low, close, 100 + index)


def _snapshot(m5: tuple[Candle, ...], *, bid: float | None = None, ask: float | None = None, context=None) -> MarketSnapshot:
    last = m5[-1].close
    bid = last if bid is None else bid
    ask = last + 0.02 if ask is None else ask
    h1 = tuple(
        Candle(NOW - timedelta(hours=30 - i), 100 + i * 0.01, 100.4 + i * 0.01, 99.6 + i * 0.01, 100 + i * 0.01, 100)
        for i in range(30)
    )
    return MarketSnapshot(
        symbol="XAUUSDc",
        captured_at_utc=NOW,
        market_time_msc=int(NOW.timestamp() * 1000),
        bid=bid,
        ask=ask,
        timeframes={"M5": TimeframeSnapshot("M5", m5), "M15": TimeframeSnapshot("M15", m5), "H1": TimeframeSnapshot("H1", h1)},
        context=context or {},
    )


def _config():
    return load_scalping_research_config(Path("config/scalping-strategies.yaml"))


def test_inside_bar_momentum_breakout_emits_buy():
    bars = [_candle(i, 100.0, 100.4, 99.6, 100.1) for i in range(25)]
    bars[-3] = _candle(22, 100.0, 101.2, 99.8, 101.0)
    bars[-2] = _candle(23, 100.8, 101.0, 100.2, 100.7)
    bars[-1] = _candle(24, 100.9, 101.5, 100.8, 101.4)
    snapshot = _snapshot(tuple(bars))
    signal = evaluate_inside_bar(snapshot, _config().strategies["inside_bar_momentum_breakout_v1"], NOW)
    live = evaluate_inside_bar_live(
        snapshot,
        bundled_strategy_snapshot().config_for("inside_bar_momentum_breakout_v1"),
        NOW,
    ).candidate
    assert signal is not None
    assert live is not None
    assert signal.side == live.side == "BUY"
    assert signal.entry == live.reference_entry
    assert signal.stop_loss == live.stop_loss
    assert signal.take_profit == live.take_profit
    assert signal.stop_loss < signal.entry < signal.take_profit


def test_ema_cross_emits_buy_on_fresh_5_9_cross_with_9_above_21():
    bars = []
    for i in range(29):
        bars.append(_candle(i, 100.0, 100.2, 99.8, 100.0))
    bars.append(_candle(29, 100.0, 103.2, 99.9, 103.0))
    signal = evaluate_ema_cross(_snapshot(tuple(bars)), _config().strategies["ema_cross_scalp_v1"], NOW)
    assert signal is not None
    assert signal.side == "BUY"
    assert signal.features["triple_alignment"] == "BULL"
    assert signal.features["ema_trigger"] > signal.features["ema_signal"] > signal.features["ema_trend"]


def test_ema_cross_rejects_5_9_cross_without_9_21_alignment():
    bars = []
    closes = [100.0 - i * 0.3 for i in range(30)]
    for i, close in enumerate(closes):
        bars.append(_candle(i, close + 0.1, close + 0.3, close - 0.3, close))
    rebound = closes[-1] + 5.0
    bars.append(_candle(30, closes[-1], rebound + 0.3, closes[-1] - 0.2, rebound))
    signal = evaluate_ema_cross(_snapshot(tuple(bars)), _config().strategies["ema_cross_scalp_v1"], NOW)
    assert signal is None


def test_breakout_retest_emits_after_break_retest_and_confirmation():
    bars = [_candle(i, 100.0, 100.4, 99.6, 100.0) for i in range(30)]
    bars[-3] = _candle(27, 100.1, 101.4, 100.0, 101.2)
    bars[-2] = _candle(28, 101.1, 101.3, 100.35, 100.8)
    bars[-1] = _candle(29, 100.8, 101.5, 100.7, 101.25)
    snapshot = _snapshot(tuple(bars))
    signal = evaluate_breakout_retest(snapshot, _config().strategies["breakout_retest_v1"], NOW)
    live = evaluate_breakout_retest_live(
        snapshot,
        bundled_strategy_snapshot().config_for("breakout_retest_v1"),
        NOW,
    ).candidate
    assert signal is not None
    assert live is not None
    assert signal.side == live.side == "BUY"
    assert signal.entry == live.reference_entry
    assert signal.stop_loss == live.stop_loss
    assert signal.take_profit == live.take_profit
    assert signal.features["bars_since_breakout"] == 2


def test_pinbar_requires_nearby_htf_support_and_emits_buy():
    bars = [_candle(i, 100.0, 100.25, 99.75, 100.0) for i in range(20)]
    bars[-1] = _candle(19, 100.0, 100.2, 98.8, 100.15)
    context = {
        "higher_timeframe_structure": {
            "status": "READY",
            "context_only": True,
            "supports": [{"center": 99.5, "distance_from_price": -0.6, "timeframe": "H4", "importance": 2.0, "touches": 2}],
            "resistances": [{"center": 105.0, "distance_from_price": 4.9, "timeframe": "D1", "importance": 1.5, "touches": 1}],
        }
    }
    signal = evaluate_pinbar(_snapshot(tuple(bars), context=context), _config().strategies["pinbar_reversal_v1"], NOW)
    assert signal is not None
    assert signal.side == "BUY"
    assert signal.features["sr_timeframe"] == "H4"
