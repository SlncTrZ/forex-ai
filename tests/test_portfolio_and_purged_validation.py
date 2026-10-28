"""Tests for Portfolio Risk Controller, Purged Cross-Validation, and Monte Carlo Ruin.
Wing: code | Topic: test_portfolio_and_purged | Updated: 2026-10-28 12:40
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.research.monte_carlo import permute_trade_sequences_with_ruin
from forex_ai.research.walkforward import PurgedSample, purge_and_embargo
from forex_ai.risk.portfolio_allocator import (
    PortfolioBudgetConfig,
    PortfolioRiskController,
    TradingHorizon,
)

UTC = timezone.utc


def test_portfolio_risk_controller_cross_horizon_conflict_block():
    controller = PortfolioRiskController()
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)

    # Đang có lệnh Day Trading BUY XAUUSD
    active_positions = [{"symbol": "XAUUSD", "side": "BUY", "horizon": TradingHorizon.DAY_TRADING}]

    # 1. Tín hiệu Scalp cùng chiều BUY XAUUSD -> Cho phép
    verdict_buy = controller.check_order_admission(
        TradingHorizon.SCALPING, "XAUUSD", "BUY", equity=10_000.0, now_utc=now, open_positions=active_positions
    )
    assert verdict_buy.approved
    assert verdict_buy.allowed_risk_pct > 0

    # 2. Tín hiệu Scalp ngược chiều SELL XAUUSD -> Tường lửa CHẶN
    verdict_sell = controller.check_order_admission(
        TradingHorizon.SCALPING, "XAUUSD", "SELL", equity=10_000.0, now_utc=now, open_positions=active_positions
    )
    assert not verdict_sell.approved
    assert "CROSS_HORIZON_CONFLICT" in verdict_sell.reason


def test_portfolio_risk_controller_circuit_breaker():
    controller = PortfolioRiskController()
    now = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)

    # Giả lập Scalping lỗ vượt trần (ngân sách 20% của 3% = 0.6% vốn = $60 trên $10k)
    controller.update_loss(TradingHorizon.SCALPING, realized_delta=70.0, unrealized_current=0.0, equity=10_000.0, now_utc=now)

    # Scalping bị khóa bởi Circuit Breaker
    verdict_scalp = controller.check_order_admission(
        TradingHorizon.SCALPING, "EURUSD", "BUY", equity=10_000.0, now_utc=now, open_positions=[]
    )
    assert not verdict_scalp.approved
    assert "CIRCUIT_BREAKER_TRIPPED" in verdict_scalp.reason

    # Day Trading vẫn độc lập và được phép hoạt động (Không bị lây nhiễm rủi ro)
    verdict_day = controller.check_order_admission(
        TradingHorizon.DAY_TRADING, "EURUSD", "BUY", equity=10_000.0, now_utc=now, open_positions=[]
    )
    assert verdict_day.approved


def test_purged_and_embargoed_cross_validation():
    t0 = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    t_test_start = datetime(2026, 10, 10, 0, 0, tzinfo=UTC)
    t_test_end = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)

    samples = [
        # Mẫu 1: Hoàn toàn trước Test -> Thuộc Train
        PurgedSample("s1", t0, t0 + timedelta(days=2)),
        # Mẫu 2: Bắt đầu trước Test nhưng kết thúc vắt qua Test -> Phải bị PURGED khỏi Train
        PurgedSample("s2", t_test_start - timedelta(days=1), t_test_start + timedelta(days=1)),
        # Mẫu 3: Nằm trọn vẹn trong Test -> Thuộc Test
        PurgedSample("s3", t_test_start + timedelta(days=1), t_test_start + timedelta(days=2)),
        # Mẫu 4: Nằm trong khoảng Embargo (ngay sau Test trong vòng 2 ngày) -> Bị loại bỏ khỏi Train
        PurgedSample("s4", t_test_end + timedelta(hours=12), t_test_end + timedelta(days=1)),
        # Mẫu 5: Hoàn toàn sau Embargo -> Thuộc Train
        PurgedSample("s5", t_test_end + timedelta(days=5), t_test_end + timedelta(days=6)),
    ]

    train_res, test_res = purge_and_embargo(samples, t_test_start, t_test_end, embargo_duration=timedelta(days=2))

    assert [s.sample_id for s in test_res] == ["s3"]
    # s2 bị purge vì overlap, s4 bị loại vì embargo -> Chỉ còn s1 và s5 trong Train
    assert [s.sample_id for s in train_res] == ["s1", "s5"]


def test_monte_carlo_sequence_shuffling_risk_of_ruin():
    # 20 lệnh: 12 lệnh thắng (+1.5R) và 8 lệnh thua (-1.0R)
    trades = [1.5] * 12 + [-1.0] * 8

    report = permute_trade_sequences_with_ruin(trades, ruin_threshold_r=5.0, iterations=1000, seed=42)

    assert report.trade_count == 20
    assert report.iterations == 1000
    assert report.max_drawdown_r_p95 > 0.0
    assert 0.0 <= report.risk_of_ruin_pct <= 100.0
