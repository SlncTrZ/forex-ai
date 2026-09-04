from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from forex_ai.execution.close_service import GuardedCloseService
from forex_ai.execution.controller import SendOutcome
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.journal.db import initialize
from forex_ai.journal.integration_repository import (
    SQLiteIntentRepository,
    TradingControlState,
    load_trade_closure,
    save_trading_control,
)
from forex_ai.mt5.contracts import BrokerDeal

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)


def make_service(tmp_path):
    db = tmp_path / "forex.db"
    initialize(db)
    save_trading_control(db, TradingControlState(True, NOW + timedelta(hours=1), False, False, "test"))
    repo = SQLiteIntentRepository(db)
    repo.save(
        OrderIntent(
            intent_id="intent-1",
            candidate_id="candidate-1",
            idempotency_key="key-1",
            symbol="EURUSD",
            side="BUY",
            volume=Decimal("0.01"),
            entry=Decimal("1.1000"),
            stop_loss=Decimal("1.0900"),
            take_profit=Decimal("1.1200"),
            state=ExecutionState.RECONCILED,
            created_at_utc=NOW,
            broker_position_ticket=77,
            filled_volume=Decimal("0.01"),
        )
    )
    return GuardedCloseService(db_path=db, execution_enabled=True, identity_guard=lambda: None), db


def test_close_is_allowed_even_when_entry_kill_switch_is_active(tmp_path):
    service, db = make_service(tmp_path)
    save_trading_control(db, TradingControlState(False, None, True, False, "kill"))
    current = service.submit_close_once(
        "intent-1",
        now_utc=NOW,
        exit_reason="RISK_KILL",
        request={"position": 77},
        final_check=lambda _: {"retcode": 0},
        is_final_check_passed=lambda response: bool(response and response["retcode"] == 0),
        send=lambda _: {"retcode": 10009, "order": 88},
        classify=lambda _: SendOutcome(True, broker_order_ticket=88),
    )
    assert current.state is ExecutionState.RECONCILED
    closure = load_trade_closure(db, "intent-1")
    assert closure["exit_reason"] == "RISK_KILL"
    assert closure["outcome_class"] == "ACCEPTED"


def test_close_reconciles_to_closed_and_preserves_reason(tmp_path):
    service, db = make_service(tmp_path)
    service.submit_close_once(
        "intent-1",
        now_utc=NOW,
        exit_reason="STRATEGY_INVALIDATION",
        request={"position": 77},
        final_check=lambda _: {"retcode": 0},
        is_final_check_passed=lambda _: True,
        send=lambda _: {"retcode": 10009},
        classify=lambda _: SendOutcome(True),
    )
    deal = BrokerDeal(
        ticket=1,
        order=2,
        position_id=77,
        symbol="EURUSD",
        volume=0.01,
        price=1.101,
        profit=-0.25,
        time_msc=1,
    )
    closed = service.reconcile_close(
        "intent-1",
        now_utc=NOW + timedelta(seconds=1),
        positions=(),
        deals=(deal,),
    )
    assert closed.state is ExecutionState.CLOSED
    assert closed.last_reason == "STRATEGY_INVALIDATION"
    closure = load_trade_closure(db, "intent-1")
    assert closure["closed_at_utc"] is not None
    assert closure["final_pnl"] == "-0.25"


def test_broker_stop_loss_exit_is_explained(tmp_path):
    service, db = make_service(tmp_path)
    deal = BrokerDeal(ticket=2,order=3,position_id=77,symbol='EURUSD',volume=0.01,price=1.09,profit=-1.0,time_msc=2,reason=4)
    closed = service.reconcile_broker_exit('intent-1',now_utc=NOW+timedelta(seconds=2),positions=(),deals=(deal,),deal_reason_sl=4,deal_reason_tp=5)
    assert closed.state is ExecutionState.CLOSED
    assert closed.last_reason == 'STOP_LOSS'
    closure = load_trade_closure(db, 'intent-1')
    assert closure['exit_reason'] == 'STOP_LOSS'
