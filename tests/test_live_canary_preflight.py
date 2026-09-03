from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from forex_ai.execution.live_canary import assess_live_canary_readiness
from forex_ai.journal.db import initialize
from forex_ai.journal.integration_repository import TradingControlState, save_trading_control
from forex_ai.risk.profile import RiskProfile
from forex_ai.runtime import ops

UTC=timezone.utc
NOW=datetime(2026,9,3,12,0,tzinfo=UTC)


def profile():
    return RiskProfile(max_risk_per_trade_pct=Decimal('1'),max_total_open_risk_pct=Decimal('3'),daily_loss_limit_pct=Decimal('3'),weekly_loss_limit_pct=Decimal('5'),max_active_orders=3)


def ready_db(tmp_path,monkeypatch):
    db=tmp_path/'forex.db';initialize(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO runtime_heartbeats(timestamp_utc,health_state,reason,payload_json) VALUES(?,?,?,?)",(NOW.isoformat(),'HEALTHY','ok','{}'))
    save_trading_control(db,TradingControlState(True,NOW+timedelta(hours=1),False,False,'live-canary'))
    monkeypatch.setattr(ops.shutil,'disk_usage',lambda _:SimpleNamespace(free=10**12))
    return db


def approval(tmp_path,approved=True):
    p=tmp_path/'strategy-approval.json'
    p.write_text(json.dumps({'approved':approved,'strategy_version':'trend_pullback_v2','evidence_fingerprint':'a'*64,'approved_at_utc':NOW.isoformat()}),encoding='utf-8')
    return p


def test_live_canary_requires_explicit_strategy_approval_and_one_symbol(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_live_canary_readiness(db_path=db,mode='LIVE_CANARY',execution_enabled=True,symbols=('EURUSD',),risk_profile=profile(),approval_path=approval(tmp_path),now_utc=NOW)
    assert report.ready
    assert report.strategy_version=='trend_pullback_v2'
    assert len(report.risk_profile_fingerprint)==64


def test_live_canary_blocks_without_strategy_approval(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_live_canary_readiness(db_path=db,mode='LIVE_CANARY',execution_enabled=True,symbols=('EURUSD',),risk_profile=profile(),approval_path=None,now_utc=NOW)
    assert not report.ready and 'STRATEGY_APPROVAL_MISSING' in report.reasons


def test_live_canary_blocks_failed_strategy_and_multiple_symbols(tmp_path,monkeypatch):
    db=ready_db(tmp_path,monkeypatch)
    report=assess_live_canary_readiness(db_path=db,mode='LIVE_CANARY',execution_enabled=True,symbols=('EURUSD','GBPUSD'),risk_profile=profile(),approval_path=approval(tmp_path,False),now_utc=NOW)
    assert not report.ready
    assert set(report.reasons)>={'STRATEGY_NOT_APPROVED','LIVE_CANARY_REQUIRES_ONE_SYMBOL'}
