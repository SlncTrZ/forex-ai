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
