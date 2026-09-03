from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from forex_ai.execution.controller import ExecutionController
from forex_ai.execution.mt5 import (
    MT5MarketRequestPolicy,
    MT5RequestError,
    MT5RetcodeClassifier,
    ProtectionDisposition,
    ProtectionPolicy,
    build_close_request,
    build_market_request,
    build_protection_request,
    intent_comment,
    order_check_passed,
    protection_disposition,
)
from forex_ai.execution.reconcile import find_orphan_positions, reconcile_intent
from forex_ai.execution.state import ExecutionState, InMemoryIntentRepository, OrderIntent
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize, session
from forex_ai.journal.integration_repository import (
    SQLiteIntentRepository,
    TradingControlState,
    save_trading_control,
)
from forex_ai.mt5.contracts import BrokerDeal, BrokerPosition, SymbolContract, TickSnapshot
from forex_ai.risk.broker_engine import BrokerRiskResult

D = Decimal
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc)


def constants():
    return {
        "TRADE_ACTION_DEAL": 1,
        "TRADE_ACTION_SLTP": 6,
        "ORDER_TYPE_BUY": 0,
        "ORDER_TYPE_SELL": 1,
        "ORDER_FILLING_FOK": 0,
        "ORDER_FILLING_IOC": 1,
        "ORDER_FILLING_RETURN": 2,
        "ORDER_TIME_GTC": 0,
        "TRADE_RETCODE_REQUOTE": 10004,
        "TRADE_RETCODE_REJECT": 10006,
        "TRADE_RETCODE_CANCEL": 10007,
        "TRADE_RETCODE_PLACED": 10008,
        "TRADE_RETCODE_DONE": 10009,
        "TRADE_RETCODE_DONE_PARTIAL": 10010,
        "TRADE_RETCODE_ERROR": 10011,
        "TRADE_RETCODE_TIMEOUT": 10012,
        "TRADE_RETCODE_INVALID": 10013,
        "TRADE_RETCODE_INVALID_VOLUME": 10014,
        "TRADE_RETCODE_INVALID_PRICE": 10015,
        "TRADE_RETCODE_INVALID_STOPS": 10016,
        "TRADE_RETCODE_TRADE_DISABLED": 10017,
        "TRADE_RETCODE_MARKET_CLOSED": 10018,
        "TRADE_RETCODE_NO_MONEY": 10019,
        "TRADE_RETCODE_PRICE_CHANGED": 10020,
        "TRADE_RETCODE_PRICE_OFF": 10021,
        "TRADE_RETCODE_TOO_MANY_REQUESTS": 10024,
        "TRADE_RETCODE_FROZEN": 10029,
        "TRADE_RETCODE_INVALID_FILL": 10030,
        "TRADE_RETCODE_CONNECTION": 10031,
        "TRADE_RETCODE_LIMIT_ORDERS": 10033,
        "TRADE_RETCODE_LIMIT_VOLUME": 10034,
        "TRADE_RETCODE_INVALID_ORDER": 10035,
        "TRADE_RETCODE_LIMIT_POSITIONS": 10040,
        "TRADE_RETCODE_LONG_ONLY": 10042,
        "TRADE_RETCODE_SHORT_ONLY": 10043,
        "TRADE_RETCODE_CLOSE_ONLY": 10044,
        "TRADE_RETCODE_HEDGE_PROHIBITED": 10046,
    }


def contract(filling_mode=3):
    return SymbolContract(
        symbol="EURUSD", digits=5, point=0.00001, trade_contract_size=100000,
        volume_min=0.01, volume_max=100, volume_step=0.01, trade_stops_level=10,
        filling_mode=filling_mode,
    )


def intent(state=ExecutionState.RISK_APPROVED, *, intent_id="intent-abc1234567890"):
    return OrderIntent(
        intent_id=intent_id, candidate_id="candidate-1", idempotency_key=f"key-{intent_id}",
        symbol="EURUSD", side="BUY", volume=D("0.10"), entry=D("1.1000"),
        stop_loss=D("1.0900"), take_profit=D("1.1200"), state=state, created_at_utc=NOW,
    )


def prepared_controller():
    repo = InMemoryIntentRepository()
    ctl = ExecutionController(repo)
    item = intent(ExecutionState.INTENT_CREATED)
    ctl.register(item)
    ctl.approve_risk(item.intent_id)
    request = {"x": 1}
    ctl.preflight(item.intent_id, request, lambda _: {"retcode": 0}, order_check_passed)
    return ctl, repo, item.intent_id, request


def test_market_request_is_deterministic_and_prefers_ioc():
    item = intent()
    request = build_market_request(
        item,
        contract=contract(filling_mode=3),
        constants=constants(),
        policy=MT5MarketRequestPolicy(deviation_points=20, magic=123456),
    )
    assert request["action"] == 1
    assert request["type"] == 0
    assert request["type_filling"] == constants()["ORDER_FILLING_IOC"]
    assert request["comment"] == intent_comment(item.intent_id)
    assert len(request["comment"]) <= 31
    assert request["sl"] == 1.09 and request["tp"] == 1.12


def test_market_request_rejects_unaligned_volume_or_price():
    bad_volume = intent().transition(ExecutionState.PREFLIGHT_PASSED, reason="test", volume=D("0.105"))
    with pytest.raises(MT5RequestError, match="volume step"):
        build_market_request(
            bad_volume, contract=contract(), constants=constants(),
            policy=MT5MarketRequestPolicy(deviation_points=0, magic=1),
        )
    bad_price = intent().transition(ExecutionState.PREFLIGHT_PASSED, reason="test", entry=D("1.100001"))
    with pytest.raises(MT5RequestError, match="broker tick size"):
        build_market_request(
            bad_price, contract=contract(), constants=constants(),
            policy=MT5MarketRequestPolicy(deviation_points=0, magic=1),
        )


def test_market_request_rejects_unapproved_or_unsupported_filling():
    with pytest.raises(MT5RequestError, match="risk-approved"):
        build_market_request(
            intent(ExecutionState.INTENT_CREATED), contract=contract(), constants=constants(),
            policy=MT5MarketRequestPolicy(deviation_points=0, magic=1),
        )
    with pytest.raises(MT5RequestError, match="neither IOC nor FOK"):
        build_market_request(
            intent(), contract=contract(filling_mode=0), constants=constants(),
            policy=MT5MarketRequestPolicy(deviation_points=0, magic=1),
        )


def test_protection_and_close_request_builders_are_deterministic():
    position = BrokerPosition(
        ticket=900, symbol="EURUSD", side="BUY", volume=0.10,
        price_open=1.1, price_current=1.101, sl=0, tp=0, comment="x",
    )
    protection = build_protection_request(
        position,
        stop_loss=D("1.0900"),
        take_profit=D("1.1200"),
        contract=contract(),
        constants=constants(),
    )
    assert protection == {
        "action": 6,
        "position": 900,
        "symbol": "EURUSD",
        "sl": 1.09,
        "tp": 1.12,
    }
    close_request = build_close_request(
        position,
        tick=TickSnapshot(symbol="EURUSD", bid=1.1009, ask=1.1011, time_msc=1, captured_at_utc=NOW),
        contract=contract(),
        constants=constants(),
        policy=MT5MarketRequestPolicy(deviation_points=25, magic=42),
    )
    assert close_request["position"] == 900
    assert close_request["type"] == constants()["ORDER_TYPE_SELL"]
    assert close_request["price"] == 1.1009
    assert close_request["type_filling"] == constants()["ORDER_FILLING_IOC"]


def test_protection_policy_repairs_then_emergency_closes():
    unprotected = BrokerPosition(
        ticket=901, symbol="EURUSD", side="BUY", volume=0.10,
        price_open=1.1, price_current=1.101, sl=0, tp=0,
    )
    policy = ProtectionPolicy(max_repair_attempts=2, emergency_close_on_failure=True)
    assert protection_disposition(
        unprotected,
        expected_stop_loss=D("1.0900"), expected_take_profit=D("1.1200"),
        contract=contract(), failed_repair_attempts=0, policy=policy,
    ) is ProtectionDisposition.REPAIR
    assert protection_disposition(
        unprotected,
        expected_stop_loss=D("1.0900"), expected_take_profit=D("1.1200"),
        contract=contract(), failed_repair_attempts=2, policy=policy,
    ) is ProtectionDisposition.EMERGENCY_CLOSE
    protected = unprotected.model_copy(update={"sl": 1.09, "tp": 1.12})
    assert protection_disposition(
        protected,
        expected_stop_loss=D("1.0900"), expected_take_profit=D("1.1200"),
        contract=contract(), failed_repair_attempts=2, policy=policy,
    ) is ProtectionDisposition.VERIFIED


def test_order_check_requires_explicit_zero_retcode():
    assert order_check_passed({"retcode": 0})
    assert not order_check_passed(None)
    assert not order_check_passed({"retcode": 10016})


@pytest.mark.parametrize("retcode", [10012, 10031])
def test_timeout_and_connection_retcodes_become_unknown(retcode):
    ctl, repo, intent_id, request = prepared_controller()
    classifier = MT5RetcodeClassifier(constants())
    result = ctl.send_once(intent_id, request, lambda _: {"retcode": retcode}, classifier.classify)
    assert result.state is ExecutionState.UNKNOWN
    assert repo.get(intent_id).state is ExecutionState.UNKNOWN
    with pytest.raises(ValueError, match="reconcile uncertain states"):
        ctl.send_once(intent_id, request, lambda _: {"retcode": 10009}, classifier.classify)


def test_empty_or_malformed_send_response_is_unknown_not_rejected():
    classifier = MT5RetcodeClassifier(constants())
    assert classifier.classify(None).unknown
    assert classifier.classify({"comment": "bad"}).unknown
    malformed_partial = classifier.classify({"retcode": 10010, "order": 1, "volume": "NaN"})
    assert malformed_partial.unknown
    assert malformed_partial.reason == "BROKER_MALFORMED_PARTIAL_VOLUME"


def test_partial_retcode_enters_partial_state_and_reconcile_can_finish_fill():
    ctl, _, intent_id, request = prepared_controller()
    classifier = MT5RetcodeClassifier(constants())
    partial = ctl.send_once(
        intent_id,
        request,
        lambda _: {"retcode": 10010, "order": 77, "deal": 88, "volume": 0.04},
        classifier.classify,
    )
    assert partial.state is ExecutionState.PARTIALLY_FILLED
    assert partial.filled_volume == D("0.04")
    assert partial.broker_order_ticket == 77
    assert partial.broker_position_ticket is None
    deals = (
        BrokerDeal(ticket=1, order=77, position_id=99, symbol="EURUSD", volume=0.04, price=1.1, time_msc=1),
        BrokerDeal(ticket=2, order=77, position_id=99, symbol="EURUSD", volume=0.06, price=1.1, time_msc=2),
    )
    position = BrokerPosition(
        ticket=99, symbol="EURUSD", side="BUY", volume=0.10, price_open=1.1, price_current=1.1,
        sl=1.09, tp=1.12, comment=intent_comment(intent_id),
    )
    reconciled = reconcile_intent(partial, deals=deals, positions=(position,))
    assert reconciled.intent.state is ExecutionState.RECONCILED
    assert reconciled.intent.filled_volume == D("0.10")


def test_transient_price_reject_is_terminal_without_blind_retry():
    ctl, _, intent_id, request = prepared_controller()
    classifier = MT5RetcodeClassifier(constants())
    rejected = ctl.send_once(intent_id, request, lambda _: {"retcode": 10020}, classifier.classify)
    assert rejected.state is ExecutionState.REJECTED
    assert rejected.last_reason == "MT5_TRANSIENT_REJECT_10020"
    with pytest.raises(ValueError):
        ctl.send_once(intent_id, request, lambda _: {"retcode": 10009}, classifier.classify)


def test_timeout_but_accepted_reconciles_by_stable_comment_after_restart(tmp_path):
    db = tmp_path / "execution.db"
    initialize(db)
    repo = SQLiteIntentRepository(db)
    ctl = ExecutionController(repo)
    item = intent(ExecutionState.INTENT_CREATED, intent_id="intent-timeout-restart")
    ctl.register(item)
    ctl.approve_risk(item.intent_id)
    ctl.preflight(item.intent_id, {}, lambda _: {"retcode": 0}, order_check_passed)
    unknown = ctl.send_once(item.intent_id, {}, lambda _: (_ for _ in ()).throw(TimeoutError()), MT5RetcodeClassifier(constants()).classify)
    assert unknown.state is ExecutionState.UNKNOWN

    reopened = SQLiteIntentRepository(db).get(item.intent_id)
    assert reopened is not None and reopened.state is ExecutionState.UNKNOWN
    broker_position = BrokerPosition(
        ticket=555, symbol="EURUSD", side="BUY", volume=0.10, price_open=1.1, price_current=1.101,
        sl=1.09, tp=1.12, comment=intent_comment(item.intent_id),
    )
    result = reconcile_intent(reopened, positions=(broker_position,))
    assert result.intent.state is ExecutionState.RECONCILED
    assert result.intent.broker_position_ticket == 555
    assert not result.blocking_reasons


def approved_risk_result(candidate_id="candidate-exec"):
    return BrokerRiskResult(
        candidate_id=candidate_id,
        approved=True,
        reason_codes=(),
        normalized_symbol="EURUSD",
        normalized_volume=D("0.10"),
        executable_entry=D("1.1000"),
        stop_loss=D("1.0900"),
        take_profit=D("1.1200"),
        projected_loss_account_currency=D("10"),
        margin_required=D("20"),
        risk_profile_fingerprint="r" * 64,
        safety_snapshot_fingerprint="s" * 64,
        expires_at_utc=NOW.replace(hour=9),
    )


def armed_service(tmp_path):
    db = tmp_path / "guarded.db"
    initialize(db)
    save_trading_control(
        db,
        TradingControlState(
            armed=True,
            arm_expires_at_utc=NOW.replace(hour=10),
            kill_switch=False,
            maintenance_mode=False,
            reason="TEST_ARM",
        ),
    )
    return GuardedExecutionService(db_path=db, execution_enabled=True), db


def test_guarded_service_persists_preflight_and_send_hashes(tmp_path):
    service, db = armed_service(tmp_path)
    item = service.create_intent(approved_risk_result(), now_utc=NOW)
    request = {"action": 1, "symbol": "EURUSD", "volume": 0.1}
    service.preflight(
        item.intent_id,
        now_utc=NOW,
        request=request,
        check=lambda _: {"retcode": 0, "comment": "Done"},
        is_passed=order_check_passed,
    )
    sent = service.send_once(
        item.intent_id,
        now_utc=NOW,
        request=request,
        send=lambda _: {"retcode": 10009, "order": 701, "deal": 702, "volume": 0.1},
        classify=MT5RetcodeClassifier(constants()).classify,
    )
    assert sent.state is ExecutionState.ACCEPTED
    with session(db) as con:
        rows = con.execute(
            "select phase,length(request_sha256),length(response_sha256),retcode,outcome_class "
            "from execution_broker_events_v1 where intent_id=? order by id",
            (item.intent_id,),
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("PREFLIGHT", 64, 64, 0, "PASSED"),
        ("SEND", 64, 64, 10009, "ACCEPTED"),
    ]


def test_guarded_service_records_send_exception_and_persists_unknown(tmp_path):
    service, db = armed_service(tmp_path)
    item = service.create_intent(approved_risk_result("candidate-exception"), now_utc=NOW)
    service.preflight(item.intent_id, now_utc=NOW, request={}, check=lambda _: {"retcode": 0}, is_passed=order_check_passed)

    def fail_send(_):
        raise RuntimeError("transport exploded after call boundary")

    result = service.send_once(
        item.intent_id,
        now_utc=NOW,
        request={"x": 1},
        send=fail_send,
        classify=MT5RetcodeClassifier(constants()).classify,
    )
    assert result.state is ExecutionState.UNKNOWN
    with session(db) as con:
        row = con.execute(
            "select outcome_class,response_sha256 from execution_broker_events_v1 where intent_id=? and phase='SEND'",
            (item.intent_id,),
        ).fetchone()
    assert tuple(row) == ("UNKNOWN_EXCEPTION", None)


def test_guarded_service_persistent_reconcile_records_full_path(tmp_path):
    service, db = armed_service(tmp_path)
    item = service.create_intent(approved_risk_result("candidate-restart"), now_utc=NOW)
    service.preflight(item.intent_id, now_utc=NOW, request={}, check=lambda _: {"retcode": 0}, is_passed=order_check_passed)
    unknown = service.send_once(
        item.intent_id,
        now_utc=NOW,
        request={},
        send=lambda _: (_ for _ in ()).throw(TimeoutError()),
        classify=MT5RetcodeClassifier(constants()).classify,
    )
    assert unknown.state is ExecutionState.UNKNOWN
    position = BrokerPosition(
        ticket=808,
        symbol="EURUSD",
        side="BUY",
        volume=0.10,
        price_open=1.1,
        price_current=1.101,
        sl=1.09,
        tp=1.12,
        comment=intent_comment(item.intent_id),
    )
    reopened_service = GuardedExecutionService(db_path=db, execution_enabled=True)
    reconciled = reopened_service.reconcile(item.intent_id, positions=(position,))
    assert reconciled.intent.state is ExecutionState.RECONCILED
    assert reopened_service.repository.get(item.intent_id).state is ExecutionState.RECONCILED
    with session(db) as con:
        states = [
            row[0]
            for row in con.execute(
                "select to_state from execution_transitions_v1 where intent_id=? order by id",
                (item.intent_id,),
            ).fetchall()
        ]
    assert states[-4:] == ["UNKNOWN", "FILLED", "PROTECTION_VERIFIED", "RECONCILED"]


def test_timeout_accepted_tagged_position_is_not_reported_as_orphan():
    item = intent(ExecutionState.UNKNOWN, intent_id="intent-timeout-tagged")
    tagged = BrokerPosition(
        ticket=557, symbol="EURUSD", side="BUY", volume=0.10, price_open=1.1, price_current=1.101,
        sl=1.09, tp=1.12, comment=intent_comment(item.intent_id),
    )
    assert find_orphan_positions((item,), (tagged,)) == ()
    foreign = tagged.model_copy(update={"ticket": 558, "comment": "manual"})
    assert find_orphan_positions((item,), (foreign,)) == (foreign,)


def test_timeout_but_accepted_without_protection_remains_blocking():
    item = intent(ExecutionState.UNKNOWN, intent_id="intent-unprotected")
    broker_position = BrokerPosition(
        ticket=556, symbol="EURUSD", side="BUY", volume=0.10, price_open=1.1, price_current=1.101,
        sl=0, tp=1.12, comment=intent_comment(item.intent_id),
    )
    result = reconcile_intent(item, positions=(broker_position,))
    assert result.intent.state is ExecutionState.FILLED
    assert "UNPROTECTED_POSITION" in result.blocking_reasons
