"""Command Center exposes bounded real audit-backed D3 telemetry only."""
from __future__ import annotations

from types import SimpleNamespace

from forex_ai.dashboard import server
from forex_ai.journal.db import initialize, log_audit_event


def test_regime_endpoint_has_no_fabricated_data(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    result = server.read_regimes()
    assert result["source"] == "forex_ai_regime_audit"
    assert result["regimes"] == []


def test_regime_view_is_latest_per_symbol_and_sanitized(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    def record(regime, symbol="XAUUSDm"):
        log_audit_event(db, event_type="D3_REGIME_DECISION", source="production_v1_scanner",
                        symbol=symbol, payload={
                            "symbol": symbol, "regime": regime,
                            "quant_regime": regime, "model_regime": regime,
                            "confidence": 0.92, "reason_code": "REGIME_CONFIRMED",
                            "closed_h1_utc": "2026-10-30T15:00:00+00:00",
                            "feature_fingerprint": "a"*64,
                            "model_fingerprint": "fprint", "latency_ms": 450,
                            "policy_version": "regime-h1-v1",
                            "token": "NEVER_OUTPUT_THIS",
                            "password": "NOT_A_PASSWORD",
                        })
    record("SIDEWAY")
    record("UPTREND")
    record("DOWNTREND", symbol="BTCUSDm")
    data = server.read_regimes()
    assert len(data["regimes"]) == 2
    xau = next(x for x in data["regimes"] if x["symbol"] == "XAUUSDm")
    assert xau["regime"] == "UPTREND" and xau["latency_ms"] == 450
    assert "token" not in xau and "password" not in xau
    assert "NEVER_OUTPUT_THIS" not in str(data)
    assert isinstance(xau["age_seconds"], int)



def test_confluence_dashboard_is_audit_only(tmp_path, monkeypatch):
    db = tmp_path / "audit.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    assert server.read_confluence()["decisions"] == []
    log_audit_event(db, event_type="D3_CONFLUENCE_DECISION",
                    source="production_v1_scanner", symbol="XAUUSDm", payload={
                        "candidate_id": "candidate-fingerprint",
                        "strategy_id": "supertrend_volume_m5",
                        "symbol": "XAUUSDm", "regime": "UPTREND",
                        "score": 76, "alignment_points": 30, "value_points": 16,
                        "momentum_points": 18, "trigger_points": 12,
                        "risk_multiplier": "0.5", "verdict": "REDUCE",
                        "reason_code": "CONFLUENCE_REDUCED", "model_fingerprint": "fp",
                        "input_fingerprint": "hash", "latency_ms": 550,
                        "policy_version": "confluence-v1",
                        "password": "SHOULD_NEVER_RENDER",
                        "api_key": "NOT_A_REAL_KEY",
                    })
    output = server.read_confluence()
    assert output["source"] == "forex_ai_confluence_audit"
    assert len(output["decisions"]) == 1
    row = output["decisions"][0]
    assert row["score"] == 76 and row["risk_multiplier"] == "0.5"
    assert "password" not in row and "api_key" not in row
    assert "SHOULD_NEVER_RENDER" not in str(output)


def test_independent_intelligence_audit_shows_unconfirmed_probabilities(tmp_path, monkeypatch):
    db = tmp_path / "intel.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    log_audit_event(db, event_type="MARKET_INTELLIGENCE_OBSERVATION",
                    source="market_intelligence_h1", symbol="BTCUSDm", payload={
                        "symbol": "BTCUSDm", "regime": "UNCERTAIN",
                        "quant_regime": "DOWNTREND", "model_regime": "DOWNTREND",
                        "confidence": .5818, "choice_probability": .8385,
                        "reason_code": "REGIME_LOW_CONFIDENCE", "market_state": "ACTIVE",
                        "closed_h1_utc": "2026-10-31T08:00:00+00:00",
                        "model_fingerprint": "fp", "feature_fingerprint": "qfp",
                        "latency_ms": 400, "policy_version": "regime-h1-v1",
                        "token": "SECRET_MUST_NOT_LEAK",
                        "confirmed_for_trading": True,
                    })
    result = server.read_regimes()
    assert len(result["regimes"]) == 1
    observation = result["regimes"][0]
    assert observation["symbol"] == "BTCUSDm"
    assert observation["market_state"] == "ACTIVE"
    assert observation["quant_regime"] == "DOWNTREND"
    assert observation["model_regime"] == "DOWNTREND"
    assert observation["regime"] == "UNCERTAIN"
    assert observation["confidence"] == .5818
    assert observation["choice_probability"] == .8385
    assert observation["confirmed_for_trading"] is False
    assert "token" not in str(observation)
