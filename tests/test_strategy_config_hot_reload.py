from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from forex_ai.journal.db import initialize
from forex_ai.journal.integration_repository import load_candidate, persist_candidate
from forex_ai.strategy.config import (
    bundled_strategy_snapshot,
    last_good_path,
    load_strategy_snapshot,
    required_raw_bars,
)
from forex_ai.strategy.v1.contracts import CandidateEnvelope


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _payload() -> dict:
    source = bundled_strategy_snapshot().source_path
    return yaml.safe_load(source.read_text(encoding="utf-8"))


def test_bundled_strategy_config_is_valid_and_production_fingerprint_is_stable():
    a = bundled_strategy_snapshot()
    b = bundled_strategy_snapshot()
    assert a.fingerprint == b.fingerprint
    assert len(a.production_fingerprint) == 64
    assert a.config_for("trend_pullback_v1").parameters["ema_fast"] == 20
    assert a.config_for("trend_pullback_v1").parameters["ema_slow"] == 50
    assert required_raw_bars(a) == 52


def test_hot_reload_changes_config_fingerprint_without_changing_strategy_version(tmp_path, monkeypatch):
    active = tmp_path / "strategy.yaml"
    payload = _payload()
    _write_yaml(active, payload)
    monkeypatch.setenv("FOREX_AI_STRATEGY_CONFIG", str(active))

    before = load_strategy_snapshot()
    old_config = before.config_for("trend_pullback_v1")

    payload["strategies"]["trend_pullback_v1"]["parameters"]["ema_fast"] = 12
    payload["strategies"]["trend_pullback_v1"]["parameters"]["ema_slow"] = 40
    _write_yaml(active, payload)

    after = load_strategy_snapshot()
    new_config = after.config_for("trend_pullback_v1")
    assert before.production_fingerprint != after.production_fingerprint
    assert old_config.version == new_config.version
    assert old_config.fingerprint != new_config.fingerprint
    assert new_config.parameters["ema_fast"] == 12
    assert new_config.parameters["ema_slow"] == 40


def test_invalid_reload_uses_last_known_good(tmp_path, monkeypatch):
    active = tmp_path / "strategy.yaml"
    payload = _payload()
    _write_yaml(active, payload)
    monkeypatch.setenv("FOREX_AI_STRATEGY_CONFIG", str(active))

    good = load_strategy_snapshot()
    assert last_good_path(active).is_file()

    payload["strategies"]["trend_pullback_v1"]["parameters"]["ema_fast"] = 100
    payload["strategies"]["trend_pullback_v1"]["parameters"]["ema_slow"] = 20
    _write_yaml(active, payload)

    fallback = load_strategy_snapshot()
    assert fallback.loaded_from_last_good
    assert fallback.rejected_error
    assert fallback.production_fingerprint == good.production_fingerprint
    assert fallback.config_for("trend_pullback_v1").parameters["ema_fast"] == 20


def test_explicit_invalid_config_fails_instead_of_silently_falling_back(tmp_path):
    path = tmp_path / "strategy.yaml"
    payload = _payload()
    payload["strategies"]["volatility_breakout_v1"]["parameters"]["min_efficiency"] = 2.0
    _write_yaml(path, payload)
    with pytest.raises(Exception):
        load_strategy_snapshot(path)


def test_required_raw_bars_tracks_ema_slow_hot_reload(tmp_path):
    path = tmp_path / "strategy.yaml"
    payload = _payload()
    payload["strategies"]["trend_pullback_v1"]["parameters"]["ema_slow"] = 100
    payload["strategies"]["trend_pullback_v1"]["parameters"]["ema_fast"] = 20
    _write_yaml(path, payload)
    snapshot = load_strategy_snapshot(path)
    assert required_raw_bars(snapshot) == 101


def test_candidate_persists_strategy_config_fingerprint(tmp_path):
    db = tmp_path / "forex.db"
    initialize(db)
    now = datetime(2026, 9, 5, 4, 0, tzinfo=timezone.utc)
    candidate = CandidateEnvelope(
        candidate_id="candidate-config-test",
        correlation_id="corr-config-test",
        strategy_id="trend_pullback_v1",
        strategy_version="1.0.0",
        symbol="EURUSDc",
        side="BUY",
        reference_entry=1.1,
        stop_loss=1.09,
        take_profit=1.12,
        generated_at_utc=now,
        market_time_msc=1,
        expires_at_utc=now + timedelta(minutes=45),
        evidence_hash="e" * 64,
        market_snapshot_fingerprint="m" * 64,
        opportunity_key="op-config-test",
        strategy_config_fingerprint="c" * 64,
    )
    persist_candidate(db, candidate)
    loaded = load_candidate(db, candidate.candidate_id)
    assert loaded is not None
    assert loaded.strategy_config_fingerprint == "c" * 64
