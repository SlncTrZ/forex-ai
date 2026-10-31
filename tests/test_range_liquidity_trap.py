"""Unit tests for Range Liquidity Trap M5 Strategy & Sideway Decision Routing.
Wing: code | Topic: test_range_liquidity_trap | Updated: 2026-10-31 20:25
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forex_ai.advisory.regime_router import RegimeDecision, route_candidate
from forex_ai.strategy.v1.contracts import (
    Candle,
    MarketSnapshot,
    StrategyConfig,
    StrategyVersion,
    TimeframeSnapshot,
)
from forex_ai.strategy.v2 import range_liquidity_trap_m5

UTC = timezone.utc


def _make_range_bars(n: int = 35, base_price: float = 2700.0, end_time: datetime | None = None) -> list[Candle]:
    t_end = end_time or datetime(2026, 10, 28, 11, 0, tzinfo=UTC)
    t0 = t_end - timedelta(minutes=5 * n)
    bars = []
    for i in range(n):
        t = t0 + timedelta(minutes=5 * i)
        offset = 1.0 if (i % 2 == 0) else -1.0
        c = base_price + offset
        bars.append(
            Candle(
                time_utc=t,
                open=c - 0.5 * offset,
                high=base_price + 2.0,
                low=base_price - 2.0,
                close=c,
                volume=100.0,
            )
        )
    return bars


def _snapshot(now: datetime, tf: TimeframeSnapshot, bid: float = 2700.0, ask: float = 2700.5) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="XAUUSDm",
        captured_at_utc=now,
        market_time_msc=int(now.timestamp() * 1000),
        bid=bid,
        ask=ask,
        timeframes={"M5": tf},
        spread_cost=ask - bid,
    )


def test_range_trap_rejects_insufficient_bars():
    cfg = StrategyConfig(StrategyVersion("range_liquidity_trap_m5", "2.0.0"), {"range_lookback_bars": 20})
    now = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)
    bars = _make_range_bars(n=15, end_time=now)
    tf = TimeframeSnapshot("M5", tuple(bars), None)
    snap = _snapshot(now, tf, 2700.0, 2700.5)
    res = range_liquidity_trap_m5.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "INSUFFICIENT_CLOSED_BARS" in res.no_setup_reason_codes


def test_range_trap_rejects_session_blackout():
    cfg = StrategyConfig(StrategyVersion("range_liquidity_trap_m5", "2.0.0"), {"session_start_hour": 6.5, "session_end_hour": 16.5})
    # Giờ rollover 22:00 UTC
    now = datetime(2026, 10, 28, 22, 15, tzinfo=UTC)
    bars = _make_range_bars(n=35, end_time=now)
    tf = TimeframeSnapshot("M5", tuple(bars), None)
    snap = _snapshot(now, tf, 2700.0, 2700.5)
    res = range_liquidity_trap_m5.evaluate(snap, cfg, now)
    assert res.candidate is None
    assert "SESSION_OR_ROLLOVER_BLACKOUT" in res.no_setup_reason_codes


def test_range_trap_detects_sell_liquidity_trap():
    cfg = StrategyConfig(
        StrategyVersion("range_liquidity_trap_m5", "2.0.0"),
        {"range_lookback_bars": 20, "session_start_hour": 6.5, "session_end_hour": 16.5, "target_r": 2.0},
    )
    now = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)
    bars = _make_range_bars(n=30, base_price=2700.0, end_time=now - timedelta(minutes=5))

    # Cây nến cuối cùng (latest): Quét thủng Range High (2702.0) lên 2704.0 rồi đóng nến đỏ tại 2701.0
    t_last = now
    bars.append(
        Candle(
            time_utc=t_last,
            open=2702.5,
            high=2704.0,  # Quét thủng 2702.0
            low=2700.8,
            close=2701.2, # Đóng nến đỏ (< open và < range_high 2702.0)
            volume=250.0,
        )
    )

    tf = TimeframeSnapshot("M5", tuple(bars), None)
    snap = _snapshot(now, tf, 2701.0, 2701.3)
    res = range_liquidity_trap_m5.evaluate(snap, cfg, now)

    assert res.candidate is not None
    assert res.candidate.side == "SELL"
    assert "RANGE_LIQUIDITY_TRAP_CONFIRMED" in res.evidence.reason_codes
    assert res.candidate.stop_loss > res.candidate.reference_entry
    assert res.candidate.take_profit < res.candidate.reference_entry


def test_range_trap_detects_buy_liquidity_trap():
    cfg = StrategyConfig(
        StrategyVersion("range_liquidity_trap_m5", "2.0.0"),
        {"range_lookback_bars": 20, "session_start_hour": 6.5, "session_end_hour": 16.5, "target_r": 2.0},
    )
    now = datetime(2026, 10, 28, 11, 0, tzinfo=UTC)
    bars = _make_range_bars(n=30, base_price=2700.0, end_time=now - timedelta(minutes=5))

    # Cây nến cuối cùng: Quét thủng Range Low (2698.0) xuống 2696.0 rồi rút chân đóng nến xanh tại 2698.8
    t_last = now
    bars.append(
        Candle(
            time_utc=t_last,
            open=2697.5,
            high=2699.2,
            low=2696.0,  # Quét thủng 2698.0
            close=2698.8, # Đóng nến xanh (> open và > range_low 2698.0)
            volume=250.0,
        )
    )

    tf = TimeframeSnapshot("M5", tuple(bars), None)
    snap = _snapshot(now, tf, 2699.0, 2699.3)
    res = range_liquidity_trap_m5.evaluate(snap, cfg, now)

    assert res.candidate is not None
    assert res.candidate.side == "BUY"
    assert "RANGE_LIQUIDITY_TRAP_CONFIRMED" in res.evidence.reason_codes
    assert res.candidate.stop_loss < res.candidate.reference_entry
    assert res.candidate.take_profit > res.candidate.reference_entry


def test_regime_router_permits_range_trap_in_sideway():
    decision = RegimeDecision(
        symbol="XAUUSDm",
        regime="SIDEWAY",
        quant_regime="SIDEWAY",
        model_regime="SIDEWAY",
        confidence=0.9,
        reason_code="REGIME_CONFIRMED",
        closed_h1_utc="2026-10-28T10:00:00+00:00",
        feature_fingerprint="fp1",
        model_fingerprint="mfp1",
        latency_ms=10,
    )
    # Range trap có evidence RANGE_LIQUIDITY_TRAP_CONFIRMED -> Không bị Veto
    veto = route_candidate(
        decision,
        strategy_id="range_liquidity_trap_m5",
        side="BUY",
        evidence_codes=("RANGE_LIQUIDITY_TRAP_CONFIRMED",),
    )
    assert veto is None

    # Chiến lược thường (như supertrend_volume_m5) trong SIDEWAY -> Bị VETO
    veto_trend = route_candidate(
        decision,
        strategy_id="supertrend_volume_m5",
        side="BUY",
        evidence_codes=("SUPERTREND_VOLUME_CONFIRMED",),
    )
    assert veto_trend == "REGIME_SIDEWAY_VETO"
