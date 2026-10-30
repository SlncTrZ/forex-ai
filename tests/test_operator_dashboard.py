from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from forex_ai.dashboard import control, server
from forex_ai.execution.account_mode import MT5AccountTradeMode
from forex_ai.journal.db import initialize
from forex_ai.journal.integration_repository import TradingControlState, load_trading_control, save_trading_control

UTC = timezone.utc


def database(tmp_path, monkeypatch, state=None):
    db = tmp_path / "dashboard.db"
    initialize(db)
    monkeypatch.setattr(control, "load_runtime_config", lambda: SimpleNamespace(db_path=db, symbols=("XAUUSD",)))
    if state is not None:
        save_trading_control(db, state)
    return db


def audit(db):
    with sqlite3.connect(db) as con:
        return con.execute("SELECT event_type,payload_json FROM audit_events WHERE source='dashboard'").fetchall()


def test_emergency_stop_is_atomic_and_does_not_touch_broker(tmp_path, monkeypatch):
    db = database(tmp_path, monkeypatch, TradingControlState(True, datetime.now(UTC)+timedelta(hours=1), False, False, "test"))
    result = control.change_control("kill", {"confirmation":"EMERGENCY STOP"})
    assert result["kill_switch"] is True
    s = load_trading_control(db)
    assert not s.armed and s.kill_switch
    events = audit(db)
    assert len(events) == 1
    assert json.loads(events[0][1])["action"] == "KILL"
    with pytest.raises(control.ControlDenied, match="KILL_ALREADY_ACTIVE"):
        control.change_control("kill", {"confirmation":"EMERGENCY STOP"})
    assert len(audit(db)) == 1


def test_disarm_preserves_existing_kill_switch(tmp_path, monkeypatch):
    db = database(tmp_path, monkeypatch, TradingControlState(True, datetime.now(UTC)+timedelta(hours=1), True, False, "test"))
    result = control.change_control("disarm", {"confirmation":"DISARM"})
    assert result["kill_switch"] is True
    assert load_trading_control(db).kill_switch


def test_arm_preflight_denial_never_writes(tmp_path, monkeypatch):
    db = database(tmp_path, monkeypatch)
    monkeypatch.setattr(control, "arm_preflight", lambda: control.ArmCheck("REAL",False,("REAL_APPROVAL_MISSING",)))
    with pytest.raises(control.ControlDenied, match="REAL_APPROVAL_MISSING"):
        control.change_control("arm", {"confirmation":"ARM REAL","minutes":60})
    assert load_trading_control(db).kill_switch
    assert audit(db) == []


def test_manual_kill_cannot_be_cleared_by_arm(tmp_path, monkeypatch):
    db = database(tmp_path, monkeypatch, TradingControlState(False,None,True,False,"MANUAL_KILL_SWITCH"))
    monkeypatch.setattr(control, "arm_preflight", lambda: control.ArmCheck("REAL",True,()))
    with pytest.raises(control.ControlDenied, match="MANUAL_SAFETY_LOCK_ACTIVE"):
        control.change_control("arm", {"confirmation":"ARM REAL","minutes":60})
    assert load_trading_control(db).kill_switch
    assert not audit(db)


def test_arm_confirmation_and_lease_duration_validation(tmp_path, monkeypatch):
    db = database(tmp_path, monkeypatch, TradingControlState(False,None,True,False,"AUTO_PREFLIGHT_BLOCKED:ACCOUNT_NOT_REAL"))
    monkeypatch.setattr(control, "arm_preflight", lambda: control.ArmCheck("DEMO",True,()))
    for payload in ({"confirmation":"ARM REAL","minutes":60},{"confirmation":"ARM DEMO","minutes":0},
                    {"confirmation":"ARM DEMO","minutes":2000},{"confirmation":"ARM DEMO","minutes":True}):
        with pytest.raises(control.ControlDenied):
            control.change_control("arm", payload)
    assert not audit(db)
    assert load_trading_control(db).kill_switch
    outcome = control.change_control("arm", {"confirmation":"ARM DEMO","minutes":15})
    assert outcome["armed"] and not outcome["kill_switch"]
    assert len(audit(db)) == 1
    with pytest.raises(control.ControlDenied, match="ALREADY_ARMED"):
        control.change_control("arm", {"confirmation":"ARM DEMO","minutes":15})
    assert len(audit(db)) == 1


def test_account_mode_read_direct_from_mt5_and_failure_denies(monkeypatch):
    class Fake:
        def __init__(self,cfg): pass
        def connect(self): return True
        def account_info(self): return {"trade_mode":2}
        def close(self): pass
    monkeypatch.setattr(control,"MT5Client",Fake)
    monkeypatch.setattr(control,"load_runtime_config",lambda:SimpleNamespace(mode="DEMO"))
    monkeypatch.setattr(control,"assert_account_matches",lambda *a,**kw:None)
    _,mode=control._fresh_account()
    assert mode == MT5AccountTradeMode.REAL
    monkeypatch.setattr(control,"assert_account_matches",lambda *a,**kw: (_ for _ in ()).throw(RuntimeError("mismatch")))
    with pytest.raises(control.ControlDenied,match="MT5_ACCOUNT_VERIFICATION_FAILED"):
        control._fresh_account()


def test_real_preflight_fails_closed_without_approval(tmp_path, monkeypatch):
    db = database(tmp_path, monkeypatch, TradingControlState(False,None,True,False,"AUTO_PREFLIGHT_BLOCKED"))
    monkeypatch.setattr(control,"_fresh_account",lambda: ({},MT5AccountTradeMode.REAL))
    monkeypatch.setattr(control,"assess_runtime_health",lambda *a,**kw:SimpleNamespace(healthy=True,reasons=()))
    monkeypatch.setattr(control,"load_execution_enabled",lambda:True)
    monkeypatch.setattr(control,"load_risk_profile",lambda:SimpleNamespace(enabled=True,kill_switch=False))
    monkeypatch.setattr(control,"auto_live_window",lambda _:True)
    monkeypatch.delenv("FOREX_AI_STRATEGY_APPROVAL_FILE",raising=False)
    report=control.arm_preflight()
    assert not report.ready and "REAL_APPROVAL_MISSING" in report.reasons


def _http(path,method="GET",headers=None,body=b"",socket_path=None):
    s=socket.socket(socket.AF_UNIX)
    s.settimeout(3)
    s.connect(str(socket_path))
    h={"Host":"127.0.0.1:8765","Connection":"close",**(headers or {})}
    h["Content-Length"]=str(len(body))
    raw=(method+" "+path+" HTTP/1.1\r\n"+"".join(k+": "+v+"\r\n" for k,v in h.items())+"\r\n").encode()+body
    s.sendall(raw)
    chunks=[]
    while True:
        try: data=s.recv(32768)
        except socket.timeout: break
        if not data:break
        chunks.append(data)
    s.close()
    response=b"".join(chunks)
    code=int(response.split(b" ")[1])
    return code,response


@pytest.fixture
def site(tmp_path):
    path=tmp_path/"dashboard.sock"
    s=server.ThreadingUnixServer(str(path),server.Handler)
    os.chmod(path,0o600)
    thread=threading.Thread(target=s.serve_forever,daemon=True)
    thread.start()
    yield path
    s.shutdown()
    s.server_close()
    thread.join(5)


def test_socket_permissions_and_static_interface(site):
    assert site.stat().st_mode & 0o777 == 0o600
    code,data=_http("/",socket_path=site)
    assert code==200 and b"FOREX" in data
    assert b"/assets/app.js" in data or b'<div id="root">' in data
    code,data=_http("/assets/app.css",socket_path=site)
    assert code==200 and b"metric-grid" in data
    code,data=_http("/api/status",headers={"Host":"evil.test"},socket_path=site)
    assert code==403
    assert b"UNTRUSTED_HOST" in data


@pytest.mark.parametrize("headers",[
    {"Origin":"http://evil.test","Content-Type":"application/json","X-Forex-AI-Action":"operator-v1"},
    {"Origin":"http://127.0.0.1:8765","Content-Type":"text/plain","X-Forex-AI-Action":"operator-v1"},
    {"Origin":"http://127.0.0.1:8765","Content-Type":"application/json"},
    {"Content-Type":"application/json","X-Forex-AI-Action":"operator-v1"},
])
def test_control_endpoint_rejects_csrf_without_mutation(site,headers,monkeypatch):
    monkeypatch.setattr(server,"change_control",lambda *a,**kw:pytest.fail("mutating control handler invoked"))
    code,_=_http("/api/control/arm","POST",headers,b'{"confirmation":"ARM REAL"}',site)
    assert code==403


def test_invalid_json_never_invokes_control(site,monkeypatch):
    monkeypatch.setattr(server,"change_control",lambda *a,**kw:pytest.fail("mutating control handler invoked"))
    code,_=_http("/api/control/arm","POST",{
        "Origin":"http://127.0.0.1:8765","Content-Type":"application/json","X-Forex-AI-Action":"operator-v1"
    },b'{"broken":',site)
    assert code==400


def test_log_scrubber_hides_account_secrets():
    assert server._safe_payload(json.dumps({"password":"never-output","login":"secret","reason":"BLOCKED","token":"hidden"})) == '{"reason": "BLOCKED"}'
