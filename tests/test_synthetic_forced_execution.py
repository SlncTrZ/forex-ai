from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from forex_ai.execution.close_service import GuardedCloseService
from forex_ai.execution.controller import SendOutcome
from forex_ai.execution.mt5 import intent_comment
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize, session
from forex_ai.journal.integration_repository import TradingControlState, save_trading_control
from forex_ai.mt5.contracts import BrokerDeal, BrokerOrder, BrokerPosition
from forex_ai.risk.broker_engine import BrokerRiskResult

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def approved_risk() -> BrokerRiskResult:
    return BrokerRiskResult(
        candidate_id="synthetic-candidate-001",
        side="BUY",
        approved=True,
        reason_codes=("SYNTHETIC_RISK_APPROVED",),
        normalized_symbol="XAUUSDc",
        normalized_volume=Decimal("0.01"),
        executable_entry=Decimal("4470.000"),
        stop_loss=Decimal("4465.000"),
        take_profit=Decimal("4480.000"),
        projected_loss_account_currency=Decimal("5.00"),
        margin_required=Decimal("20.00"),
        risk_profile_fingerprint="r" * 64,
        safety_snapshot_fingerprint="s" * 64,
        expires_at_utc=NOW + timedelta(minutes=5),
    )


def test_synthetic_forced_full_execution_lifecycle(tmp_path):
    db = tmp_path / "synthetic.db"
    initialize(db)
    save_trading_control(
        db,
        TradingControlState(True, NOW + timedelta(minutes=15), False, False, "SYNTHETIC_DRILL"),
    )

    risk = approved_risk()
    service = GuardedExecutionService(db_path=db, execution_enabled=True, identity_guard=lambda: None)
    intent = service.create_intent(risk, now_utc=NOW)
    request = {
        "symbol": "XAUUSDc",
        "side": "BUY",
        "volume": 0.01,
        "price": 4470.0,
        "sl": 4465.0,
        "tp": 4480.0,
        "comment": intent_comment(intent.intent_id),
    }

    intent = service.preflight(
        intent.intent_id,
        now_utc=NOW,
        request=request,
        check=lambda _: {"retcode": 0},
        is_passed=lambda result: result is not None and result["retcode"] == 0,
    )
    intent = service.send_once(
        intent.intent_id,
        now_utc=NOW + timedelta(milliseconds=10),
        request=request,
        send=lambda _: {"retcode": 10009, "order": 90001, "deal": 91001},
        classify=lambda _: SendOutcome(
            True,
            broker_order_ticket=90001,
            broker_position_ticket=92001,
            reason="SYNTHETIC_BROKER_ACCEPTED",
        ),
        fresh_revalidate=lambda _: risk,
        final_check=lambda _: {"retcode": 0},
        is_final_check_passed=lambda result: result is not None and result["retcode"] == 0,
    )

    comment = intent_comment(intent.intent_id)
    order = BrokerOrder(
        ticket=90001,
        symbol="XAUUSDc",
        volume_initial=0.01,
        volume_current=0.0,
        price_open=4470.0,
        sl=4465.0,
        tp=4480.0,
        comment=comment,
    )
    open_deal = BrokerDeal(
        ticket=91001,
        order=90001,
        position_id=92001,
        symbol="XAUUSDc",
        volume=0.01,
        price=4470.0,
        profit=0.0,
        time_msc=1,
        comment=comment,
    )
    position = BrokerPosition(
        ticket=92001,
        symbol="XAUUSDc",
        side="BUY",
        volume=0.01,
        price_open=4470.0,
        price_current=4471.0,
        sl=4465.0,
        tp=4480.0,
        profit=1.0,
        comment=comment,
    )

    intent = service.reconcile(
        intent.intent_id,
        orders=(order,),
        deals=(open_deal,),
        positions=(position,),
    ).intent
    assert intent.state.value == "RECONCILED"

    close = GuardedCloseService(db_path=db, execution_enabled=True, identity_guard=lambda: None)
    close_request = {
        "position": 92001,
        "symbol": "XAUUSDc",
        "side": "SELL",
        "volume": 0.01,
        "price": 4472.0,
    }
    close.submit_close_once(
        intent.intent_id,
        now_utc=NOW + timedelta(seconds=2),
        exit_reason="SYNTHETIC_TEST_EXIT",
        request=close_request,
        final_check=lambda _: {"retcode": 0},
        is_final_check_passed=lambda result: result is not None and result["retcode"] == 0,
        send=lambda _: {"retcode": 10009, "order": 90002, "deal": 91002},
        classify=lambda _: SendOutcome(True, broker_order_ticket=90002, reason="SYNTHETIC_CLOSE_ACCEPTED"),
    )
    close_deal = BrokerDeal(
        ticket=91002,
        order=90002,
        position_id=92001,
        symbol="XAUUSDc",
        volume=0.01,
        price=4472.0,
        profit=2.0,
        time_msc=2,
    )
    closed = close.reconcile_close(
        intent.intent_id,
        now_utc=NOW + timedelta(seconds=3),
        positions=(),
        deals=(close_deal,),
    )
    assert closed.state.value == "CLOSED"
    assert closed.last_reason == "SYNTHETIC_TEST_EXIT"

    with session(db) as con:
        transitions = [row[0] for row in con.execute(
            "SELECT to_state FROM execution_transitions_v1 ORDER BY id"
        ).fetchall()]
        phases = [(row[0], row[1]) for row in con.execute(
            "SELECT phase,outcome_class FROM execution_broker_events_v1 ORDER BY id"
        ).fetchall()]
        closure = con.execute(
            "SELECT exit_reason,outcome_class,final_pnl FROM trade_closures_v1"
        ).fetchone()

    assert transitions == [
        "INTENT_CREATED",
        "RISK_APPROVED",
        "PREFLIGHT_PASSED",
        "SEND_STARTED",
        "ACCEPTED",
        "FILLED",
        "PROTECTION_VERIFIED",
        "RECONCILED",
        "CLOSED",
    ]
    assert phases == [
        ("PREFLIGHT", "PASSED"),
        ("FINAL_PREFLIGHT", "PASSED"),
        ("SEND", "ACCEPTED"),
        ("CLOSE_PREFLIGHT", "PASSED"),
        ("CLOSE_SEND", "ACCEPTED"),
    ]
    assert tuple(closure) == ("SYNTHETIC_TEST_EXIT", "CLOSED_RECONCILED", "2.0")
