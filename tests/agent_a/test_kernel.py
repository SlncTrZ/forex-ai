from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from forex_ai.execution.controller import ExecutionController, SendOutcome
from forex_ai.execution.reconcile import find_orphan_positions, reconcile_intent, reconciliation_blockers
from forex_ai.execution.state import ExecutionState, InMemoryIntentRepository, OrderIntent
from forex_ai.kernel.health import BackoffPolicy, HealthKernel, HealthState
from forex_ai.mt5.contracts import (
    AccountSnapshot,
    Bar,
    BarSeries,
    BrokerPosition,
    BrokerState,
    SafetySnapshot,
    SymbolContract,
    TickSnapshot,
)
from forex_ai.mt5.symbols import resolve_symbol_strict
from forex_ai.risk.broker_engine import ActiveExposure, BrokerAwareRiskEngine, CandidateInput, RiskContext
from forex_ai.risk.profile import RiskProfile

NOW = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
D = Decimal


def profile(**overrides):
    data = dict(
        max_risk_per_trade_pct=D("1"), max_total_open_risk_pct=D("3"),
        daily_loss_limit_pct=D("3"), weekly_loss_limit_pct=D("5"), max_active_orders=3,
        min_margin_reserve_pct=D("0"),
    )
    data.update(overrides)
    return RiskProfile(**data)


def account():
    return AccountSnapshot(
        login=123, server="Broker-Demo", currency="USD", balance=1000, equity=1000,
        margin=0, margin_free=1000, leverage=100, captured_at_utc=NOW,
    )


def contract():
    return SymbolContract(
        symbol="EURUSD", digits=5, point=0.00001, trade_contract_size=100000,
        volume_min=0.01, volume_max=100, volume_step=0.01, trade_stops_level=10,
    )


def tick():
    return TickSnapshot(symbol="EURUSD", bid=1.0999, ask=1.1000, time_msc=1, captured_at_utc=NOW)


def safety(reconciled=True, reasons=()):
    return SafetySnapshot(
        account_fingerprint="a" * 64, contracts_fingerprint="b" * 64,
        reconciled=reconciled, blocking_reasons=reasons, captured_at_utc=NOW,
    )


def candidate(**overrides):
    data = dict(
        candidate_id="c1", symbol="EURUSD", side="BUY", reference_entry=D("1.1000"),
        stop_loss=D("1.0900"), take_profit=D("1.1200"), expires_at_utc=NOW + timedelta(minutes=5),
    )
    data.update(overrides)
    return CandidateInput(**data)


def exposure(intent_id, risk="5", group=None):
    return ActiveExposure(intent_id=intent_id, risk_amount=D(risk), correlation_group=group)


def context(**overrides):
    data = dict(daily_reference_equity=D("1000"), weekly_reference_equity=D("1000"))
    data.update(overrides)
    return RiskContext(**data)


def calc_profit(side, symbol, volume, entry, stop):
    del side, symbol
    return (stop - entry) * D("100000") * volume


def calc_margin(side, symbol, volume, entry):
    del side, symbol
    return entry * D("100000") * volume / D("100")


def evaluate(*, p=None, c=None, ctx=None, con=None, s=None):
    return BrokerAwareRiskEngine(p or profile()).evaluate(
        c or candidate(), account=account(), contract=con or contract(), tick=tick(), safety=s or safety(),
        context=ctx or context(), calc_profit=calc_profit, calc_margin=calc_margin, now_utc=NOW,
    )


def test_profile_fingerprint_is_canonical_and_stable():
    assert profile().fingerprint == profile().fingerprint
    assert len(profile().fingerprint) == 64


def test_profile_rejects_inverted_percentage_and_absolute_limits():
    with pytest.raises(ValueError):
        profile(max_total_open_risk_pct=D("0.5"))
    with pytest.raises(ValueError):
        profile(weekly_loss_limit_pct=D("2"))
    with pytest.raises(ValueError):
        profile(max_risk_per_trade_amount=D("20"), max_total_open_risk_amount=D("10"))


def test_fourth_active_order_is_rejected_and_partial_intent_counts_once():
    ctx = context(exposures=(exposure("a"), exposure("b"), exposure("c"), exposure("c", "2")))
    result = evaluate(ctx=ctx)
    assert not result.approved
    assert "MAX_ACTIVE_ORDERS" in result.reason_codes
    assert ctx.active_orders == 3


def test_two_active_intents_allow_third():
    result = evaluate(ctx=context(exposures=(exposure("a"), exposure("b"))))
    assert result.approved
    assert result.normalized_volume == D("0.01")
    assert result.projected_loss_account_currency <= D("10")


def test_daily_weekly_reference_equity_adjusts_for_cash_flow():
    daily = evaluate(ctx=context(daily_reference_equity=D("900"), daily_net_cash_flow=D("100"), daily_realized_loss_amount=D("30")))
    weekly = evaluate(ctx=context(weekly_reference_equity=D("900"), weekly_net_cash_flow=D("100"), weekly_realized_loss_amount=D("50")))
    assert "DAILY_LOSS_LIMIT" in daily.reason_codes
    assert "WEEKLY_LOSS_LIMIT" in weekly.reason_codes


def test_missing_reference_equity_fails_closed():
    result = evaluate(ctx=RiskContext())
    assert "MISSING_DAILY_REFERENCE_EQUITY" in result.reason_codes
    assert "MISSING_WEEKLY_REFERENCE_EQUITY" in result.reason_codes


def test_total_and_correlated_open_risk_are_derived_from_exposures():
    result = evaluate(ctx=context(
        exposures=(exposure("a", "20", "USD"), exposure("b", "5", "USD")), proposed_correlation_group="USD"
    ))
    assert not result.approved
    assert "TOTAL_OPEN_RISK_LIMIT" in result.reason_codes
    assert "CORRELATED_RISK_LIMIT" in result.reason_codes


def test_absolute_per_trade_cap_is_stricter_than_percentage_cap():
    result = evaluate(p=profile(max_risk_per_trade_pct=D("5"), max_total_open_risk_pct=D("6"), max_risk_per_trade_amount=D("15")))
    assert result.approved
    assert result.projected_loss_account_currency <= D("15")


def test_volume_is_floored_not_rounded_up():
    result = evaluate(p=profile(max_risk_per_trade_pct=D("1.99")))
    assert result.approved
    assert result.normalized_volume == D("0.01")
    assert result.projected_loss_account_currency <= D("19.9")


def test_minimum_lot_overflow_is_rejected():
    expensive = contract().model_copy(update={"volume_min": 1.0, "volume_step": 1.0})
    result = evaluate(con=expensive)
    assert not result.approved
    assert "MIN_VOLUME_EXCEEDS_RISK" in result.reason_codes


def test_expiry_is_enforced_and_propagated():
    expired = candidate(expires_at_utc=NOW)
    result = evaluate(c=expired)
    assert not result.approved
    assert "CANDIDATE_EXPIRED" in result.reason_codes
    assert result.expires_at_utc == NOW


def test_blocked_safety_snapshot_rejects_entry():
    result = evaluate(s=safety(False, ("UNRESOLVED_UNKNOWN",)))
    assert "SAFETY_STATE_BLOCKED" in result.reason_codes


def test_health_kernel_blocks_account_drift_and_unprotected_position():
    hk = HealthKernel()
    hk.begin_connect(); hk.begin_sync()
    state = BrokerState(account=account(), contracts=(contract(),), ticks=(tick(),), reconciled_at_utc=NOW)
    assert hk.complete_sync(state).reconciled
    hk.degrade(); hk.begin_sync()
    changed = account().model_copy(update={"login": 999})
    pos = BrokerPosition(ticket=1, symbol="EURUSD", volume=0.01, price_open=1.1, price_current=1.1, sl=0, tp=1.12)
    second = hk.complete_sync(state.model_copy(update={"account": changed, "positions": (pos,)}))
    assert hk.state is HealthState.BLOCKED
    assert {"ACCOUNT_IDENTITY_DRIFT", "UNPROTECTED_POSITION"}.issubset(second.blocking_reasons)


def test_backoff_is_bounded():
    b = BackoffPolicy(base_seconds=1, max_seconds=4, jitter_ratio=0, rng=lambda: 0.5)
    assert [b.delay(i) for i in range(5)] == [1, 2, 4, 4, 4]


def make_intent(state=ExecutionState.UNKNOWN):
    return OrderIntent(
        intent_id="i1", candidate_id="c1", idempotency_key="k1", symbol="EURUSD", side="BUY",
        volume=D("0.01"), entry=D("1.1"), stop_loss=D("1.09"), take_profit=D("1.12"),
        state=state, created_at_utc=NOW, broker_order_ticket=11, broker_position_ticket=22,
    )


def test_unknown_without_broker_evidence_remains_blocking():
    result = reconcile_intent(make_intent())
    assert result.intent.state is ExecutionState.UNKNOWN
    assert result.blocking_reasons == ("UNRESOLVED_UNKNOWN",)


def test_unknown_with_filled_protected_position_reconciles():
    pos = BrokerPosition(ticket=22, symbol="EURUSD", volume=0.01, price_open=1.1, price_current=1.11, sl=1.09, tp=1.12)
    result = reconcile_intent(make_intent(), positions=(pos,))
    assert result.intent.state is ExecutionState.RECONCILED
    assert not result.blocking_reasons


def test_unprotected_position_blocks_reconciliation():
    pos = BrokerPosition(ticket=22, symbol="EURUSD", volume=0.01, price_open=1.1, price_current=1.11, sl=0, tp=1.12)
    assert "UNPROTECTED_POSITION" in reconcile_intent(make_intent(), positions=(pos,)).blocking_reasons


def test_repository_rejects_duplicate_idempotency_key():
    repo = InMemoryIntentRepository(); repo.save(make_intent())
    repo.save(make_intent().transition(ExecutionState.ACCEPTED, reason="evidence"))
    with pytest.raises(ValueError):
        repo.save(OrderIntent(**{**make_intent().__dict__, "intent_id": "i2"}))


def test_execution_controller_persists_send_started_before_broker_call_and_timeout_becomes_unknown():
    repo = InMemoryIntentRepository(); ctl = ExecutionController(repo)
    intent = make_intent(ExecutionState.INTENT_CREATED)
    ctl.register(intent); ctl.approve_risk(intent.intent_id)
    ctl.preflight(intent.intent_id, {}, lambda _: {"ok": True}, lambda r: bool(r and r["ok"]))

    def timeout_send(_):
        assert repo.get(intent.intent_id).state is ExecutionState.SEND_STARTED
        raise TimeoutError

    result = ctl.send_once(intent.intent_id, {}, timeout_send, lambda _: SendOutcome(True))
    assert result.state is ExecutionState.UNKNOWN
    with pytest.raises(ValueError):
        ctl.send_once(intent.intent_id, {}, lambda _: {}, lambda _: SendOutcome(True))


def test_execution_controller_duplicate_registration_is_idempotent():
    repo = InMemoryIntentRepository(); ctl = ExecutionController(repo)
    first = ctl.register(make_intent(ExecutionState.INTENT_CREATED))
    duplicate = OrderIntent(**{**first.__dict__, "intent_id": "i2"})
    assert ctl.register(duplicate).intent_id == first.intent_id


def test_orphan_position_detection():
    known = BrokerPosition(ticket=22, symbol="EURUSD", volume=0.01, price_open=1.1, price_current=1.1, sl=1.09, tp=1.12)
    orphan = known.model_copy(update={"ticket": 23})
    assert find_orphan_positions((make_intent(),), (known, orphan)) == (orphan,)


def test_bar_series_rejects_duplicate_or_unordered_bars():
    b = Bar(time_utc=NOW, open=1, high=2, low=0.5, close=1.5, tick_volume=1)
    with pytest.raises(ValueError):
        BarSeries(symbol="EURUSD", timeframe_seconds=60, closed_bars=(b, b))


def test_strict_symbol_resolution_rejects_ambiguity():
    items = [{"name": "XAUUSDm"}, {"name": "XAUUSD.pro"}]
    assert resolve_symbol_strict("XAUUSD", items) is None
    assert resolve_symbol_strict("EURUSD", [{"name": "EURUSD.a"}]) == "EURUSD.a"


def test_stale_tick_slippage_and_closed_session_fail_closed():
    closed = contract().model_copy(update={"session_open": False})
    result = evaluate(con=closed, ctx=context(tick_age_seconds=D("6"), expected_slippage_points=D("51")))
    assert "STALE_TICK" in result.reason_codes
    assert "SLIPPAGE_LIMIT" in result.reason_codes
    assert "SESSION_CLOSED" in result.reason_codes


def test_reconciliation_blockers_aggregate_unknown_orphan_and_protection():
    unprotected_orphan = BrokerPosition(ticket=23, symbol="EURUSD", volume=0.01, price_open=1.1, price_current=1.1, sl=0, tp=1.12)
    blockers = reconciliation_blockers((make_intent(),), (unprotected_orphan,))
    assert {"UNRESOLVED_UNKNOWN", "ORPHAN_BROKER_POSITION", "UNPROTECTED_POSITION"}.issubset(blockers)
