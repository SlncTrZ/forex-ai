from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from forex_ai.execution.demo_campaign import assess_demo_campaign_readiness
from forex_ai.journal.db import initialize
from forex_ai.journal.integration_repository import TradingControlState, save_trading_control
from forex_ai.runtime import ops

UTC=timezone.utc
NOW=datetime(2026,9,3,12,0,tzinfo=UTC)


def ready_db(tmp_path,monkeypatch):
    db=tmp_path/'forex.db';initialize(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO runtime_heartbeats(timestamp_utc,health_state,reason,payload_json) VALUES(?,?,?,?)",(NOW.isoformat(),'HEALTHY','ok','{}'))
    save_trading_control(db,TradingControlState(True,NOW+timedelta(hours=1),False,False,'demo'))
    monkeypatch.setattr(ops.shutil,'disk_usage',lambda _:SimpleNamespace(free=10**12))
    return db


def test_demo_campaign_requires_all_independent_interlocks(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_demo_campaign_readiness(db_path=db,mode='DEMO',execution_enabled=True,campaign_id='demo-1',account_trade_mode=0,account_identity_bound=True,now_utc=NOW)
    assert report.ready and report.reasons==()


def test_demo_campaign_fails_closed_on_wrong_mode_disabled_and_missing_id(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_demo_campaign_readiness(db_path=db,mode='OBSERVE',execution_enabled=False,campaign_id='',account_trade_mode=0,account_identity_bound=True,now_utc=NOW)
    assert not report.ready
    assert set(report.reasons)>={'MODE_NOT_DEMO','EXECUTION_DISABLED','CAMPAIGN_ID_REQUIRED'}


def test_demo_campaign_fails_on_kill_switch_and_disarm(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    save_trading_control(db,TradingControlState(False,None,True,False,'safe'))
    report=assess_demo_campaign_readiness(db_path=db,mode='DEMO',execution_enabled=True,campaign_id='demo-1',account_trade_mode=0,account_identity_bound=True,now_utc=NOW)
    assert not report.ready
    assert set(report.reasons)>={'CONTROL_DISARMED','KILL_SWITCH_ACTIVE','ARM_EXPIRED'}


def test_demo_campaign_rejects_real_account(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_demo_campaign_readiness(db_path=db,mode='DEMO',execution_enabled=True,campaign_id='demo-1',account_trade_mode=2,account_identity_bound=True,now_utc=NOW)
    assert not report.ready and 'ACCOUNT_NOT_DEMO' in report.reasons


def test_demo_campaign_rejects_missing_account_binding(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_demo_campaign_readiness(db_path=db,mode='DEMO',execution_enabled=True,campaign_id='demo-1',account_trade_mode=0,account_identity_bound=False,now_utc=NOW)
    assert not report.ready and 'ACCOUNT_BINDING_MISSING' in report.reasons
