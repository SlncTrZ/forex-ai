"""Tests for Realistic Event-Driven Engine, Dynamic Spread, Slippage, and Simulated MT5 Account.
Wing: code | Topic: test_realistic_engine | Updated: 2026-10-28 12:32
"""

from __future__ import annotations

from datetime import datetime, timezone

from forex_ai.research.models.dynamic_spread import DynamicSpreadModel
from forex_ai.research.models.simulated_account import SimulatedBrokerAccount
from forex_ai.research.models.slippage_impact import SlippageModel
from forex_ai.research.realistic_engine import RealisticEventEngine
from forex_ai.strategy.v1.contracts import (
    CandidateEnvelope,
    Candle,
    DecisionEvidence,
    MarketSnapshot,
    StrategyConfig,
    StrategyResult,
    StrategyVersion,
    TimeframeSnapshot,
)

UTC = timezone.utc


def _make_bar(t: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(time_utc=t, open=o, high=h, low=l, close=c, volume=100.0)


def test_dynamic_spread_spikes_during_rollover():
    model = DynamicSpreadModel()
    normal_time = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)  # 14:00 UTC (New York)
    rollover_time = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)  # 22:00 UTC (Rollover peak)

    normal_spread = model.calculate_spread("XAUUSD", normal_time)
    rollover_spread = model.calculate_spread("XAUUSD", rollover_time)

    assert normal_spread == 0.18
    assert rollover_spread >= 0.18 * 7.5  # Bùng nổ gấp ~8 lần lúc 22:00 UTC


def test_dynamic_spread_scales_with_volatility_and_news():
    model = DynamicSpreadModel()
    t = datetime(2026, 10, 28, 14, 0, tzinfo=UTC)

    base = model.calculate_spread("EURUSD", t)
    vol_spread = model.calculate_spread("EURUSD", t, atr_ratio=2.0)
    news_spread = model.calculate_spread("EURUSD", t, is_news_window=True)

    assert vol_spread > base
    assert news_spread >= base * 4.0


def test_slippage_model_applies_asymmetric_impact():
    model = SlippageModel(seed=123)

    # Limit order (Take Profit) receives zero or positive slippage
    limit_slip = model.calculate_slippage("EURUSD", "LIMIT", volume=1.0)
    assert limit_slip <= 0.0

    # Stop Loss order receives adverse slippage
    stop_slip = model.calculate_slippage("EURUSD", "STOP", volume=1.0)
    assert stop_slip > 0.0


def test_simulated_broker_account_margin_and_commission():
    account = SimulatedBrokerAccount(initial_balance=10_000.0, leverage=100.0)
    t = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)

    # Mở 1 lot EURUSD tại giá 1.1000
    pos = account.open_position("pos1", "EURUSD", "BUY", 1.0, 1.1000, t, 1.0950, 1.1100)
    # Commission $3.50 mở vị thế
    assert account.balance == 10_000.0 - 3.50

    # Ký quỹ: 1.0 * 100,000 * 1.1000 / 100 = 1,100.0
    margin = account.calculate_margin("EURUSD", 1.0, 1.1000)
    assert round(margin, 2) == 1100.0

    # Đóng lệnh có lãi tại 1.1050 (+50 pips = $500 gross - $3.5 commission = $496.5 net)
    net_pnl = account.close_position("pos1", 1.1050, t)
    assert round(net_pnl, 2) == 496.50
    assert round(account.balance, 2) == 10493.00


def test_simulated_broker_account_wednesday_triple_swap():
    account = SimulatedBrokerAccount(initial_balance=10_000.0)
    t_open = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    account.open_position("pos1", "EURUSD", "BUY", 1.0, 1.1000, t_open, 1.0900, 1.1200)

    # Đêm thứ Ba (Tuesday 22:00 UTC) -> 1x swap
    t_tue = datetime(2026, 10, 27, 22, 0, tzinfo=UTC)
    account.process_daily_rollover(t_tue, {"EURUSD": {"long": -10.0, "short": 2.0}})
    assert account.positions["pos1"].accumulated_swap == -10.0

    # Đêm thứ Tư (Wednesday 22:00 UTC) -> 3x swap (-30.0)
    t_wed = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)
    account.process_daily_rollover(t_wed, {"EURUSD": {"long": -10.0, "short": 2.0}})
    assert account.positions["pos1"].accumulated_swap == -40.0


def test_realistic_event_engine_simulation_run():
    t0 = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
    bar1 = _make_bar(t0, 2700.0, 2705.0, 2698.0, 2704.0)

    def dummy_strategy(snap, cfg, now):
        cand = CandidateEnvelope(
            candidate_id="cand1",
            correlation_id="corr1",
            strategy_id="TEST",
            strategy_version="1.0.0",
            symbol="XAUUSD",
            side="BUY",
            reference_entry=2704.0,
            stop_loss=2695.0,
            take_profit=2720.0,
            generated_at_utc=now,
            market_time_msc=int(now.timestamp() * 1000),
            expires_at_utc=datetime(2026, 10, 28, 18, 0, tzinfo=UTC),
            evidence_hash="hash1",
            market_snapshot_fingerprint=snap.fingerprint,
        )
        return StrategyResult(
            candidate=cand,
            invalidation=None,
            evidence=DecisionEvidence(reason_codes=("TEST_PASSED",), values={}),
        )

    cfg = StrategyConfig(StrategyVersion("test", "1"), {})
    engine = RealisticEventEngine(dummy_strategy, cfg, initial_balance=5_000.0)

    snap = MarketSnapshot(
        symbol="XAUUSD",
        captured_at_utc=t0,
        market_time_msc=int(t0.timestamp() * 1000),
        bid=2703.9,
        ask=2704.1,
        timeframes={"M5": TimeframeSnapshot("M5", [bar1], None)},
    )

    engine.process_snapshot(snap, t0)
    assert len(engine.account.positions) == 1

    # Thanh nến tiếp theo chạm Take Profit
    t1 = datetime(2026, 10, 28, 10, 5, tzinfo=UTC)
    bar2 = _make_bar(t1, 2704.0, 2725.0, 2703.0, 2722.0)
    snap2 = MarketSnapshot(
        symbol="XAUUSD",
        captured_at_utc=t1,
        market_time_msc=int(t1.timestamp() * 1000),
        bid=2721.9,
        ask=2722.1,
        timeframes={"M5": TimeframeSnapshot("M5", [bar1, bar2], None)},
    )

    engine.process_snapshot(snap2, t1)
    rep = engine.generate_report()

    assert rep.total_trades == 1
    assert rep.winning_trades == 1
    assert rep.win_rate_pct == 100.0
    assert rep.total_net_pnl > 0
