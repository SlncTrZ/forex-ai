"""Real-path mandatory D3 safety gate, zero-generation protocol and failure modes."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from forex_ai.advisory.ollama_safety_gate import (
    D3DecisionSafetyGate, GateUnavailable, SafetyVerdict, _verdict, _deterministic_block,
)
from forex_ai.execution.state import ExecutionState
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.execution.mt5 import MT5RetcodeClassifier, order_check_passed
from forex_ai.journal.integration_repository import TradingControlState, save_trading_control
from test_gate3_execution import approved_risk_result, armed_service, constants, intent


def _attempt(tmp_path, *, gate):
    service, db = armed_service(tmp_path)
    now = datetime.now(timezone.utc)
    save_trading_control(db, TradingControlState(
        armed=True, arm_expires_at_utc=now + timedelta(hours=1),
        kill_switch=False, maintenance_mode=False, reason="TEST_ARM",
    ))
    fresh = replace(
        approved_risk_result("candidate-d3"), expires_at_utc=now + timedelta(minutes=30),
    )
    service.require_safety_gate = True
    service.safety_gate = gate
    prepared = service.create_intent(fresh, now_utc=now)
    service.preflight(
        prepared.intent_id, now_utc=now, request={"action": 1},
        check=lambda _: {"retcode": 0}, is_passed=order_check_passed,
    )
    called = []

    def send(_):
        called.append("ORDER_SEND")
        return {"retcode": 10009, "order": 1001}

    def fresh_risk(_):
        called.append("FRESH_RISK")
        return fresh

    result = service.send_once(
        prepared.intent_id, now_utc=now, request={"action": 1},
        send=send, classify=MT5RetcodeClassifier(constants()).classify,
        fresh_revalidate=fresh_risk,
        final_check=lambda _: {"retcode": 0},
        is_final_check_passed=order_check_passed,
    )
    with sqlite3.connect(db) as connection:
        audit = connection.execute(
            "SELECT payload_json FROM audit_events "
            "WHERE event_type='AI_SAFETY_GATE_DECISION'"
        ).fetchall()
    assert len(audit) == 1
    return result, called, json.loads(audit[0][0])


def _reply(choice="execute", confidence=0.96, p_execute=0.96):
    return {
        "results": {
            "action": {
                "choice": choice,
                "confidence": confidence,
                "probabilities": {"execute": p_execute, "reject": round(1-p_execute, 5)},
            },
            "safety_score": {"score": 0.87},
        }
    }


def test_missing_gate_blocks_before_any_execution(tmp_path):
    row, calls, audit = _attempt(tmp_path, gate=None)
    assert row.state is ExecutionState.REJECTED
    assert row.last_reason == "AI_SAFETY_GATE_UNAVAILABLE"
    assert calls == []
    assert audit["decision"] == "UNAVAILABLE"


def test_model_blocks_without_broker_send(tmp_path):
    row, calls, audit = _attempt(
        tmp_path, gate=lambda *_: SafetyVerdict(False, "D3_MODEL_REJECT", "fprint", 401, "a"*64),
    )
    assert row.state is ExecutionState.REJECTED
    assert calls == []
    assert audit["decision"] == "BLOCK"
    assert audit["latency_ms"] == 401
    assert "D3_MODEL_REJECT" in row.last_reason


def test_model_allows_only_then_fresh_risk_and_send(tmp_path):
    row, calls, audit = _attempt(
        tmp_path, gate=lambda *_: SafetyVerdict(True, "D3_EXECUTE_PASS", "fprint", 388, "a"*64),
    )
    assert row.state is ExecutionState.ACCEPTED
    assert calls == ["FRESH_RISK", "ORDER_SEND"]
    assert audit["decision"] == "ALLOW"


def test_model_timeout_fails_closed(tmp_path):
    row, calls, audit = _attempt(
        tmp_path, gate=lambda *_: (_ for _ in ()).throw(TimeoutError()),
    )
    assert row.state is ExecutionState.REJECTED
    assert calls == []
    assert audit["decision"] == "UNAVAILABLE"


def test_boolean_fake_verdict_cannot_bypass(tmp_path):
    row, calls, _ = _attempt(tmp_path, gate=lambda *_: True)
    assert row.state is ExecutionState.REJECTED
    assert calls == []


def test_kill_switch_activated_during_decision_blocks_send(tmp_path):
    def change_control(*_):
        save_trading_control(tmp_path / "guarded.db", TradingControlState(
            armed=False, arm_expires_at_utc=None,
            kill_switch=True, maintenance_mode=False, reason="EMERGENCY_STOP",
        ))
        return SafetyVerdict(True, "D3_EXECUTE_PASS", "fprint", 355, "a"*64)

    row, calls, audit = _attempt(tmp_path, gate=change_control)
    assert row.state is ExecutionState.REJECTED
    assert row.last_reason == "AI_SAFETY_GATE_CONTROL_EXPIRED"
    assert not calls
    assert audit["decision"] == "ALLOW"


def test_local_hard_gate_blocks_wrong_stop_even_if_model_would_allow(monkeypatch):
    synthetic = replace(intent(), stop_loss=__import__("decimal").Decimal("1.1200"))
    assert _deterministic_block(synthetic, {}) == "D3_BUY_PROTECTION_CONFLICT"
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid hard rule must not contact model")
    monkeypatch.setattr(httpx, "Client", forbidden)
    gate = D3DecisionSafetyGate(endpoint="https://decision.example.invalid", api_key="synthetic")
    result = gate(synthetic, {})
    assert not result.allowed
    assert result.reason_code == "D3_BUY_PROTECTION_CONFLICT"
    assert result.latency_ms == 0


def test_local_hard_gate_rejects_broker_request_mismatch():
    synthetic = intent()
    assert _deterministic_block(synthetic, {"symbol": "GBPUSD"}) == "D3_ORDER_SYMBOL_MISMATCH"
    assert _deterministic_block(synthetic, {"volume": "0.2"}) == "D3_ORDER_REQUEST_MISMATCH"
    assert _deterministic_block(synthetic, {"sl": "1.15"}) == "D3_ORDER_REQUEST_MISMATCH"


def test_real_env_keys_are_supported(monkeypatch):
    monkeypatch.setenv("DECISION_MODEL_URL", "https://decision.example.invalid/api/v1/decisions")
    monkeypatch.setenv("DECISION_MODEL_API_KEY", "synthetic-key")
    monkeypatch.setenv("OLLAMA_KEY", "secondary-key")
    model = D3DecisionSafetyGate.from_environment()
    assert model.model == "d3:4b"
    assert model._api_key == "synthetic-key"
    assert model.endpoint.endswith("/api/v1/decisions")


def test_result_parser_requires_consistent_choice_confidence_and_probabilities():
    assert _verdict(_reply()) == (True, "D3_EXECUTE_PASS")
    assert _verdict(_reply("reject", 0.98, 0.02)) == (False, "D3_MODEL_REJECT")
    assert _verdict(_reply("execute", 0.65, 0.78)) == (False, "D3_UNCERTAIN_EXECUTE")
    assert _verdict({"action": _reply()["results"]["action"]}) == (True, "D3_EXECUTE_PASS")
    assert _verdict({"data": _reply()}) == (True, "D3_EXECUTE_PASS")
    for invalid in (
        {"results": {"action": {"choice": "execute", "confidence": 1}}},
        {"results": {"action": {"choice": "buy", "confidence": 1,
                                "probabilities": {"execute": 1, "reject": 0}}}},
        {"results": {"action": {"choice": "execute", "confidence": 1,
                                "probabilities": {"execute": 0.3, "reject": 0.2}}}},
        {"results": {"action": {"choice": "execute", "confidence": float("nan"),
                                "probabilities": {"execute": 1, "reject": 0}}}},
    ):
        with pytest.raises(GateUnavailable):
            _verdict(invalid)


def test_d3_http_requires_private_ip_and_no_url_credentials():
    for url in (
        "http://8.8.8.8:11434",
        "http://127.0.0.1:11434",
        "http://model.example.invalid",
        "http://192.168.1.18:11434@outside.invalid",
        "ftp://192.168.1.18:11434",
        "https://decision.example.invalid/a/different/path",
    ):
        with pytest.raises(GateUnavailable):
            D3DecisionSafetyGate(endpoint=url, api_key="synthetic")
    assert D3DecisionSafetyGate(
        endpoint="https://decision.example.invalid", api_key="synthetic",
    ).endpoint.endswith("/api/v1/decisions")


def test_d3_request_uses_scoring_questions_and_authorization(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self): return self
        def __exit__(self, *args): return False

        def post(self, url, json, headers):
            calls.append((url, json, headers))
            return httpx.Response(
                200, json=_reply(), request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    gate = D3DecisionSafetyGate(
        endpoint="https://decision.example.invalid", model="d3:4b", api_key="synthetic",
    )
    result = gate(intent(), {
        "symbol": "EURUSD", "volume": .1, "price": 1.1, "sl": 1.09,
        "tp": 1.12, "password": "not-a-real-credential",
    }, context={"strategy_id": "v1", "account_trade_mode": "DEMO",
                "account_login": "never-disclose"})
    assert result.allowed
    assert len(result.request_digest) == 64
    url, payload, headers = calls[0]
    assert url.endswith("/api/v1/decisions")
    assert payload["model"] == "d3:4b"
    assert isinstance(payload["state"], str)
    assert payload["questions"]["action"]["type"] == "choice"
    assert payload["questions"]["safety_score"]["type"] == "score"
    assert headers["Authorization"].startswith("Bearer ")
    assert "password" not in payload["state"]
    assert "account_login" not in payload["state"]
    assert "credential" not in payload["state"]


def test_invalid_d3_response_fails_closed(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):pass
        def __enter__(self):return self
        def __exit__(self, *args):return False
        def post(self, url, json, headers):
            return httpx.Response(
                200, json={"data": {"unexpected": "shape"}},
                request=httpx.Request("POST", url),
            )
    monkeypatch.setattr(httpx, "Client", FakeClient)
    gate = D3DecisionSafetyGate(
        endpoint="https://decision.example.invalid", api_key="synthetic",
    )
    with pytest.raises(GateUnavailable, match="D3_ACTION_MISSING"):
        gate(intent(), {})


def test_secret_required_before_network():
    with pytest.raises(GateUnavailable, match="D3_AUTH_NOT_CONFIGURED"):
        D3DecisionSafetyGate(
            endpoint="https://decision.example.invalid", api_key="",
        )


def test_dashboard_audit_reveals_decision_but_not_sensitive_fields():
    from forex_ai.dashboard.server import _safe_payload
    text = _safe_payload(json.dumps({
        "decision": "BLOCK", "reason_code": "D3_MODEL_REJECT",
        "latency_ms": 375, "model_fingerprint": "fprint",
        "password": "do-not-emit", "token": "do-not-emit",
    }))
    assert '"decision": "BLOCK"' in text
    assert '"latency_ms": 375' in text
    assert "do-not-emit" not in text
