from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from forex_ai.research.dataset import freeze_replay_dataset, load_frozen_replay_dataset
from forex_ai.research.oos import OOSAcceptancePolicy, assess_oos, run_walk_forward_fold, split_events
from forex_ai.research.replay import CostModel, ReplayEngine, ReplayEvent
from forex_ai.research.walkforward import Fold
from forex_ai.strategy.v1.contracts import (
    Candle,
    DecisionEvidence,
    MarketSnapshot,
    StrategyConfig,
    StrategyResult,
    StrategyVersion,
    TimeframeSnapshot,
    build_candidate,
)

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def snapshot(clock: datetime, index: int) -> MarketSnapshot:
    price = 100.0 + index * 0.1
    bar = Candle(clock - timedelta(minutes=15), price, price + 2.0, price - 0.5, price + 0.1, 100)
    return MarketSnapshot(
        symbol="TEST",
        captured_at_utc=clock,
        market_time_msc=int(clock.timestamp() * 1000),
        bid=price - 0.01,
        ask=price,
        timeframes={"M15": TimeframeSnapshot("M15", (bar,))},
        spread_cost=0.01,
        commission_cost=0.0,
        metadata={"source": "fixture", "index": index},
    )


def events() -> tuple[ReplayEvent, ...]:
    return tuple(ReplayEvent(START + timedelta(hours=i), snapshot(START + timedelta(hours=i), i)) for i in range(7))


CONFIG = StrategyConfig(StrategyVersion("fixture_oos", "1"), {})


def fixture_strategy(market: MarketSnapshot, config: StrategyConfig, now: datetime) -> StrategyResult:
    evidence = DecisionEvidence(("FIXTURE",), {"clock": now.isoformat()})
    candidate = build_candidate(
        snapshot=market,
        config=config,
        side="BUY",
        entry=market.ask,
        stop_loss=market.ask - 1.0,
        take_profit=market.ask + 1.0,
        generated_at_utc=now,
        expires_at_utc=now + timedelta(hours=2),
        evidence=evidence,
    )
    return StrategyResult(candidate, None, evidence)


def fold() -> Fold:
    return Fold(
        train_start=START,
        train_end=START + timedelta(hours=2),
        validation_start=START + timedelta(hours=2),
        validation_end=START + timedelta(hours=4),
        test_start=START + timedelta(hours=4),
        test_end=START + timedelta(hours=6),
    )


def test_freeze_load_roundtrip_and_manifest_are_deterministic(tmp_path):
    rows = events()
    created = datetime(2026, 9, 3, tzinfo=UTC)
    first_path = tmp_path / "first.jsonl"
    second_path = tmp_path / "second.jsonl"
    first_manifest = freeze_replay_dataset(rows, data_path=first_path, source_id="fixture-v1", created_at_utc=created)
    second_manifest = freeze_replay_dataset(rows, data_path=second_path, source_id="fixture-v1", created_at_utc=created)
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first_manifest == second_manifest
    assert first_manifest.fingerprint == second_manifest.fingerprint
    loaded = load_frozen_replay_dataset(first_path)
    assert loaded.manifest == first_manifest
    from forex_ai.research.dataset import event_payload
    assert tuple(event_payload(event) for event in loaded.events) == tuple(event_payload(event) for event in rows)


def test_frozen_dataset_refuses_overwrite_and_detects_tamper(tmp_path):
    path = tmp_path / "dataset.jsonl"
    freeze_replay_dataset(events(), data_path=path, source_id="fixture-v1", created_at_utc=START)
    with pytest.raises(FileExistsError):
        freeze_replay_dataset(events(), data_path=path, source_id="fixture-v1", created_at_utc=START)
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace('"bid":99.99', '"bid":99.98', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="byte hash mismatch"):
        load_frozen_replay_dataset(path)


def test_split_boundaries_are_non_overlapping():
    rows = events()
    f = fold()
    train = split_events(rows, f, "train")
    validation = split_events(rows, f, "validation")
    test = split_events(rows, f, "test")
    assert [event.clock_utc for event in train] == [START, START + timedelta(hours=1)]
    assert [event.clock_utc for event in validation] == [START + timedelta(hours=2), START + timedelta(hours=3)]
    assert [event.clock_utc for event in test] == [START + timedelta(hours=4), START + timedelta(hours=5)]
    train_clocks = {event.clock_utc for event in train}
    validation_clocks = {event.clock_utc for event in validation}
    test_clocks = {event.clock_utc for event in test}
    assert train_clocks.isdisjoint(validation_clocks)
    assert validation_clocks.isdisjoint(test_clocks)


def test_walk_forward_evidence_is_reproducible_and_bound_to_dataset(tmp_path):
    path = tmp_path / "dataset.jsonl"
    freeze_replay_dataset(events(), data_path=path, source_id="fixture-v1", created_at_utc=START)
    dataset = load_frozen_replay_dataset(path)
    engine = ReplayEngine(fixture_strategy, CONFIG, CostModel(spread=0.01, slippage=0.01))
    first = run_walk_forward_fold(dataset, fold=fold(), engine=engine, account_r_value=10)
    second = run_walk_forward_fold(dataset, fold=fold(), engine=engine, account_r_value=10)
    assert first.fingerprint == second.fingerprint
    assert first.dataset_manifest_fingerprint == dataset.manifest.fingerprint
    assert first.strategy_config_fingerprint == CONFIG.fingerprint
    assert first.cost_model_fingerprint == engine.cost_model.fingerprint
    assert first.test.event_count == 2
    assert first.test.metrics.trade_count == 1
    assert first.test.metrics.expectancy_r > 0


def test_oos_acceptance_is_explicit_policy_not_engine_default(tmp_path):
    path = tmp_path / "dataset.jsonl"
    freeze_replay_dataset(events(), data_path=path, source_id="fixture-v1", created_at_utc=START)
    dataset = load_frozen_replay_dataset(path)
    evidence = run_walk_forward_fold(dataset, fold=fold(), engine=ReplayEngine(fixture_strategy, CONFIG))
    permissive = assess_oos(evidence, OOSAcceptancePolicy(min_test_trades=1, min_expectancy_r=0.0))
    assert permissive.approved
    strict = assess_oos(
        evidence,
        OOSAcceptancePolicy(min_test_trades=10, min_expectancy_r=2.0, require_positive_ci_lower_bound=True),
    )
    assert not strict.approved
    assert "INSUFFICIENT_OOS_TRADES" in strict.reason_codes
    assert "OOS_EXPECTANCY_BELOW_THRESHOLD" in strict.reason_codes
