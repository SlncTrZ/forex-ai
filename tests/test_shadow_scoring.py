"""P2 shadow scoring: deterministic evidence, freshness, read-only API and no order rights."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from forex_ai.dashboard import server
from forex_ai.journal.db import initialize, session
from forex_ai.market.shadow_scoring import (
    EVENT, POLICY, THRESHOLD, WEIGHTS, observe_score, score_matrix,
)
from forex_ai.market.signal_matrix import COLUMNS, ROWS

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _matrix(symbol="BTCUSDm", when=NOW, *, active=True, enabled=True, states=None):
    states = states or {c: "BULL" for c, _ in COLUMNS}
    rows = []
    for i, (id_, name, timeframe, core) in enumerate(ROWS):
        btc = i == 0
        eligible = btc if symbol.startswith("BTC") else not btc
        rows.append({
            "strategy_id": id_, "label": name, "timeframe": timeframe,
            "enabled": enabled and i == 0, "eligible": eligible,
            "cells": [{"indicator": key, "state": states.get(key, "UNKNOWN"),
                       "core": key in core, "value": 1, "rule": key}
                      for key, _ in COLUMNS],
        })
    return {
        "symbol": symbol, "market_state": "ACTIVE" if active else "OFF_SESSION",
        "observed_at": when.isoformat(), "fingerprint": "a" * 64, "rows": rows,
    }


def _regime(when=NOW, **kw):
    return {
        "symbol": "BTCUSDm", "market_state": "ACTIVE",
        "reason_code": "REGIME_LOW_CONFIDENCE",
        "quant_regime": "UPTREND", "model_regime": "UPTREND",
        "confidence": 0.31, "choice_probability": 0.59,
        "observed_at": when.isoformat(), "closed_h1_utc": (when - timedelta(minutes=30)).isoformat(),
        "feature_fingerprint": "b" * 64, **kw,
    }


def test_weight_budget_and_full_bull_score_with_low_confidence_advisory():
    assert sum(WEIGHTS.values()) == 100
    result = score_matrix(_matrix(), regime=_regime(), now_utc=NOW)
    btc = result["results"][0]
    assert result["diagnostic_only"] is True
    assert btc["verdict"] == "SHADOW_THRESHOLD_MET"
    assert btc["best_direction"] == "BUY"
    assert btc["best_score"] == 90  # News and history have zero earned points.
    buy, sell = btc["scenarios"]
    assert buy["score"] == 90 and sell["score"] == 0
    assert buy["available_points"] == 90
    assert [p["points"] for p in buy["components"]] == [28, 20, 16, 14, 12, 0, 0]
    assert result["d3_observation_valid"] is True
    assert len(result["results"]) == 8
    assert all(r["verdict"] == "NOT_APPLICABLE" for r in result["results"][1:])


def test_no_confidence_threshold_gate_but_no_fabricated_d3_points():
    bad = _regime()
    bad["reason_code"] = "REGIME_INVALID_RESPONSE"
    r = score_matrix(_matrix(), regime=bad, now_utc=NOW)
    buy = r["results"][0]["scenarios"][0]
    assert buy["score"] == 78
    assert buy["components"][4]["available"] == 0
    assert r["source_regime_fingerprint"] == ""
    assert r["d3_observation_valid"] is False
    assert r["results"][0]["verdict"] == "SHADOW_THRESHOLD_MET"
    # The shadow label is not an MT5 admission, even when D3 is missing.


def test_bear_scenario_and_conflicting_d3_do_not_add_wrong_side_points():
    states = {key: "BEAR" for key, _ in COLUMNS}
    result = score_matrix(_matrix(states=states), regime=_regime(), now_utc=NOW)
    best = result["results"][0]
    assert best["best_direction"] == "SELL"
    assert best["best_score"] == 78
    assert best["scenarios"][1]["components"][4]["points"] == 0


def test_missing_stale_and_inactive_never_present_permission():
    missing = {key: "UNKNOWN" for key, _ in COLUMNS}
    for matrix, why in (
        (_matrix(states=missing), "INSUFFICIENT_EVIDENCE"),
        (_matrix(when=NOW - timedelta(hours=1)), "STALE"),
        (_matrix(active=False), "STALE"),
        (_matrix(enabled=False), "DISABLED"),
    ):
        output = score_matrix(matrix, regime=_regime(), now_utc=NOW)
        assert output["results"][0]["verdict"] == why
        assert output["results"][0]["best_score"] in (None, 0)
        assert output["diagnostic_only"] is True


def test_partial_setup_cannot_be_labeled_threshold_met():
    states = {key: "BULL" for key, _ in COLUMNS}
    states["SQZ"] = "NEUTRAL"
    result = score_matrix(_matrix(states=states), regime=_regime(), now_utc=NOW)
    btc = result["results"][0]
    assert btc["best_score"] == 76
    assert btc["verdict"] == "NO_COMPLETE_SETUP"


def test_audit_idempotency_and_sanitized_api(tmp_path, monkeypatch):
    db = tmp_path / "p2.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    assert server.read_shadow_scores()["scores"] == []
    result = score_matrix(_matrix(), regime=_regime(), now_utc=NOW)
    result["secret"] = "SHOULD_NOT_APPEAR"
    observe_score(db, result)
    observe_score(db, result)
    with session(db) as con:
        count = con.execute("SELECT COUNT(*) FROM audit_events WHERE event_type=?", (EVENT,)).fetchone()[0]
    assert count == 1
    response = server.read_shadow_scores()
    assert response["source"] == "forex_ai_shadow_score_audit"
    assert response["diagnostic_only"] is True
    assert len(response["scores"]) == 1
    assert response["scores"][0]["rows"][0]["best_score"] == 90
    assert "SHOULD_NOT_APPEAR" not in str(response)
    assert response["threshold"] == THRESHOLD and response["policy_version"] == POLICY


def test_tampered_audit_is_ignored(tmp_path, monkeypatch):
    db = tmp_path / "p2.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    result = score_matrix(_matrix(), regime=_regime(), now_utc=NOW)
    observe_score(db, result)
    with session(db) as con:
        raw = con.execute("SELECT payload_json FROM audit_events WHERE event_type=?", (EVENT,)).fetchone()[0]
        import json
        payload = json.loads(raw)
        payload["results"][0]["scenarios"][0]["components"][0]["points"] = 1000
        con.execute("UPDATE audit_events SET payload_json=? WHERE event_type=?",
                    (json.dumps(payload), EVENT))
    assert server.read_shadow_scores()["scores"] == []



def test_no_direction_without_any_indicators():
    unknown = {key: "UNKNOWN" for key, _ in COLUMNS}
    result = score_matrix(_matrix(states=unknown), regime=_regime(), now_utc=NOW)
    row = result["results"][0]
    assert row["best_direction"] is None
    assert row["best_score"] == 0
    assert row["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_missing_d3_and_new_fields_do_not_raise_trade_permission():
    output = score_matrix(_matrix(), regime=None, now_utc=NOW)
    row = output["results"][0]
    assert row["best_score"] == 78
    assert row["verdict"] == "SHADOW_THRESHOLD_MET"
    assert output["diagnostic_only"] is True
    # Execution code must not import this research-only module.
    from pathlib import Path
    project = Path(__file__).resolve().parents[1]
    for file in ("scripts/scan_candidates.py", "scripts/execute_pending.py",
                 "src/forex_ai/integration/execution.py"):
        assert "shadow_scoring" not in (project / file).read_text()



def test_dashboard_hides_expired_score_and_stale_market(tmp_path, monkeypatch):
    db = tmp_path / "shadow-stale.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    old = NOW - timedelta(hours=1)
    matrix = _matrix(when=old)
    historic = score_matrix(matrix, regime=_regime(when=old), now_utc=old)
    observe_score(db, historic)
    result = server.read_shadow_scores()
    assert len(result["scores"]) == 1
    assert result["scores"][0]["market_state"] == "STALE"
    row = result["scores"][0]["rows"][0]
    assert row["verdict"] == "STALE"
    assert row["best_score"] is None
    assert row["best_direction"] is None
    assert row["scenarios"] == []


def test_conflicting_model_regime_not_rewarded():
    conflicting = _regime()
    conflicting["model_regime"] = "DOWNTREND"
    output = score_matrix(_matrix(), regime=conflicting, now_utc=NOW)
    assert not output["d3_observation_valid"]
    assert output["results"][0]["scenarios"][0]["components"][4]["points"] == 0
