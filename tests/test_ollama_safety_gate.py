"""Pre-execution Ollama gate: real send path, fail-closed, strict JSON, audit."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from forex_ai.advisory.ollama_safety_gate import GateUnavailable, OllamaSafetyGate, SafetyVerdict
from forex_ai.execution.state import ExecutionState
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.execution.mt5 import MT5RetcodeClassifier, order_check_passed
from test_gate3_execution import approved_risk_result, armed_service, constants
from forex_ai.journal.integration_repository import TradingControlState, save_trading_control


def _attempt(tmp_path, *, gate, required=True):
    service, db = armed_service(tmp_path)
    now = datetime.now(timezone.utc)
    save_trading_control(db, TradingControlState(
        armed=True, arm_expires_at_utc=now+timedelta(hours=1),
        kill_switch=False, maintenance_mode=False, reason="TEST_ARM",
    ))
    fresh = replace(approved_risk_result("candidate-ollama"), expires_at_utc=now+timedelta(minutes=30))
    service.require_safety_gate = required
    service.safety_gate = gate
    item = service.create_intent(fresh, now_utc=now)
    service.preflight(item.intent_id, now_utc=now, request={"action":1},
                      check=lambda _: {"retcode":0}, is_passed=order_check_passed)
    called=[]
    def send(_):
        called.append("MT5_ORDER_SEND")
        return {"retcode":10009,"order":999}
    def revalidate(_):
        called.append("FRESH_RISK")
        return fresh
    result = service.send_once(
        item.intent_id, now_utc=now, request={"action":1},
        send=send, classify=MT5RetcodeClassifier(constants()).classify,
        fresh_revalidate=revalidate,
        final_check=lambda _: {"retcode":0}, is_final_check_passed=order_check_passed,
    )
    with sqlite3.connect(db) as conn:
        rows=conn.execute("SELECT payload_json FROM audit_events WHERE event_type='AI_SAFETY_GATE_DECISION'").fetchall()
    assert len(rows)==1
    return result,called,json.loads(rows[0][0])


def test_gate_unconfigured_rejects_before_any_fresh_risk_or_order_send(tmp_path):
    out,calls,audit=_attempt(tmp_path,gate=None)
    assert out.state is ExecutionState.REJECTED
    assert out.last_reason=="AI_SAFETY_GATE_UNAVAILABLE"
    assert calls==[]
    assert audit["decision"]=="UNAVAILABLE"


def test_ollama_veto_stops_order_and_is_audited(tmp_path):
    verdict=SafetyVerdict(False,"STOP_SIDE_CONFLICT","model16",42,"a"*64)
    out,calls,audit=_attempt(tmp_path,gate=lambda *_: verdict)
    assert out.state is ExecutionState.REJECTED
    assert out.last_reason=="AI_SAFETY_GATE_BLOCK:STOP_SIDE_CONFLICT"
    assert calls==[]
    assert audit["decision"]=="BLOCK"
    assert audit["model_fingerprint"]=="model16"
    assert audit["latency_ms"]==42


def test_ollama_allow_continues_to_fresh_risk_and_order_send(tmp_path):
    verdict=SafetyVerdict(True,"NO_BLOCKER_FOUND","model16",22,"a"*64)
    out,calls,audit=_attempt(tmp_path,gate=lambda *_: verdict)
    assert out.state is ExecutionState.ACCEPTED
    assert calls==["FRESH_RISK","MT5_ORDER_SEND"]
    assert audit["decision"]=="ALLOW"


def test_gate_allow_cannot_override_kill_switch_activated_during_inference(tmp_path):
    def change_control(*_):
        save_trading_control(
            tmp_path/"guarded.db",
            TradingControlState(False,None,True,False,"OPERATOR_EMERGENCY_STOP"),
        )
        return SafetyVerdict(True,"NO_BLOCKER_FOUND","model16",50,"a"*64)
    out,calls,audit=_attempt(tmp_path,gate=change_control)
    assert out.state is ExecutionState.REJECTED
    assert out.last_reason=="AI_SAFETY_GATE_CONTROL_EXPIRED"
    assert calls==[]
    assert audit["decision"]=="ALLOW"


def test_gate_timeout_fails_closed_without_send(tmp_path):
    out,calls,audit=_attempt(tmp_path,gate=lambda *_: (_ for _ in ()).throw(TimeoutError()))
    assert out.state is ExecutionState.REJECTED
    assert calls==[]
    assert audit["decision"]=="UNAVAILABLE"


def test_gate_does_not_allow_raw_boolean_to_bypass(tmp_path):
    out,calls,_=_attempt(tmp_path,gate=lambda *_: True)
    assert out.state is ExecutionState.REJECTED
    assert calls==[]


def test_dashboard_surfaces_gate_metadata_but_never_secrets():
    from forex_ai.dashboard.server import _safe_payload
    text = _safe_payload(json.dumps({
        "decision": "BLOCK", "reason_code": "STOP_SIDE_CONFLICT",
        "latency_ms": 71, "model_fingerprint": "model16",
        "password": "must_never_appear", "token": "hidden",
    }))
    assert '"decision": "BLOCK"' in text
    assert '"latency_ms": 71' in text
    assert "must_never_appear" not in text
    assert "hidden" not in text


def test_bad_endpoints_are_denied():
    for url in ("http://8.8.8.8:11434","http://127.0.0.1:11434",
                "http://example.com:11434","http://192.168.1.20:11434@evil.test",
                "ftp://192.168.1.20:11434"):
        with pytest.raises(GateUnavailable):
            OllamaSafetyGate(endpoint=url,model="decision-model")
    assert OllamaSafetyGate(endpoint="http://192.168.1.20:11434",model="decision-model").model=="decision-model"


def test_provider_accepts_bound_structured_output_without_credentials(monkeypatch):
    seen=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,json):
            seen.append((url,json))
            prompt=__import__("json").loads(json["messages"][1]["content"])
            body={"done":True,"message":{"content":__import__("json").dumps({
                "intent_id":prompt["intent"]["intent_id"],
                "request_digest":prompt["request_digest"],
                "decision":"ALLOW","reason_code":"NO_BLOCKER_FOUND"
            })}}
            return httpx.Response(200, json=body, request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx,"Client",FakeClient)
    from test_gate3_execution import intent
    item=intent()
    gate=OllamaSafetyGate(endpoint="http://192.168.1.20:11434",model="safety-v1")
    result=gate(item,{"symbol":"EURUSD"})
    assert result.allowed and result.reason_code=="NO_BLOCKER_FOUND"
    assert seen and seen[0][0]=="http://192.168.1.20:11434/api/chat"
    assert "password" not in __import__("json").dumps(seen[0][1]).lower()
    assert "MT5_LOGIN" not in __import__("json").dumps(seen[0][1])
    assert "broker_request" in __import__("json").loads(seen[0][1]["messages"][1]["content"])
    assert "stream" in seen[0][1] and not seen[0][1]["stream"]


def test_provider_rejects_mismatched_intent_digest(monkeypatch):
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,json):
            inp=__import__("json").loads(json["messages"][1]["content"])
            return httpx.Response(200,json={"done":True,"message":{"content":__import__("json").dumps({
                "intent_id":inp["intent"]["intent_id"],"request_digest":"a"*64,
                "decision":"ALLOW","reason_code":"NO_BLOCKER_FOUND"
            })}},request=httpx.Request("POST",url))
    monkeypatch.setattr(httpx,"Client",FakeClient)
    from test_gate3_execution import intent
    gate=OllamaSafetyGate(endpoint="http://192.168.1.20:11434",model="safety-v1")
    with pytest.raises(GateUnavailable,match="REQUEST_MISMATCH"):
        gate(intent(),{})
