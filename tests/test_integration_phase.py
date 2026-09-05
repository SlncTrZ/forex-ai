from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from forex_ai.advisory.models import Advisory, AdvisoryAction, AdvisoryStatus
from forex_ai.config import RuntimeConfig, load_risk_profile
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.integration.adapters import candidate_input, timeframe_snapshot
from forex_ai.integration.advisory_compat import legacy_review_to_provider_result
from forex_ai.integration.engine import DecisionOrchestrator, StrategyBinding
from forex_ai.integration.execution import ExecutionDisarmed, GuardedExecutionService
from forex_ai.intelligence.schemas import ReviewDecision
from forex_ai.journal.db import SCHEMA_VERSION, initialize, session
from forex_ai.journal.integration_repository import (
    SQLiteIntentRepository,
    TradingControlState,
    load_trading_control,
    save_trading_control,
)
from forex_ai.mt5.contracts import AccountSnapshot, SafetySnapshot, SymbolContract, TickSnapshot
from forex_ai.risk.broker_engine import BrokerRiskResult, RiskContext
from forex_ai.risk.profile import RiskProfile
from forex_ai.strategy.v1.contracts import DecisionEvidence, MarketSnapshot, StrategyConfig, StrategyResult, StrategyVersion, build_candidate
from forex_ai.runtime.trading_engine import build_integration_services

UTC = timezone.utc
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
D = Decimal


def test_schema_v6_and_default_control_fail_closed(tmp_path):
    db = tmp_path / "test.db"
    initialize(db)
    with session(db) as con:
        version = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert version == str(SCHEMA_VERSION)
    assert {"order_intents_v1", "execution_transitions_v1", "risk_decisions_v1", "candidate_decisions"}.issubset(tables)
    control = load_trading_control(db)
    assert not control.armed and control.kill_switch
    assert not control.allows_new_entries(now_utc=NOW)


def _intent(state=ExecutionState.INTENT_CREATED):
    return OrderIntent(
        intent_id="intent-1", candidate_id="candidate-1", idempotency_key="key-1", symbol="EURUSD", side="BUY",
        volume=D("0.01"), entry=D("1.1"), stop_loss=D("1.09"), take_profit=D("1.12"),
        state=state, created_at_utc=NOW,
    )


def test_sqlite_intent_repository_survives_reopen_and_records_transitions(tmp_path):
    db = tmp_path / "test.db"; initialize(db)
    repo = SQLiteIntentRepository(db)
    repo.save(_intent())
    repo.save(_intent().transition(ExecutionState.RISK_APPROVED, reason="RISK_APPROVED"))
    reopened = SQLiteIntentRepository(db)
    restored = reopened.get("intent-1")
    assert restored is not None and restored.state is ExecutionState.RISK_APPROVED
    assert reopened.get_by_idempotency_key("key-1").intent_id == "intent-1"
    with session(db) as con:
        states = [row[0] for row in con.execute("SELECT to_state FROM execution_transitions_v1 WHERE intent_id='intent-1' ORDER BY id")]
    assert states == ["INTENT_CREATED", "RISK_APPROVED"]


def test_legacy_llm_never_manufactures_veto_or_direction():
    decision = ReviewDecision(
        action="NO_TRADE", confidence=0.99, thesis="macro conflict", invalidation="none",
        risk_flags=["news"], web_search_used=True,
    )
    result = legacy_review_to_provider_result(decision, model_fingerprint="model-x")
    assert result.status is AdvisoryStatus.AVAILABLE
    assert result.suggested_action is AdvisoryAction.NO_CHANGE
    assert result.evidence is not None and not result.evidence.source_backed and not result.evidence.material_conflict
    unavailable = legacy_review_to_provider_result(None, model_fingerprint="model-x", provider_error="timeout")
    assert unavailable.status is AdvisoryStatus.UNAVAILABLE


def test_timeframe_adapter_rejects_out_of_order_broker_data():
    raw = [
        {"time": int((NOW - timedelta(minutes=15)).timestamp()), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "tick_volume": 10},
        {"time": int((NOW - timedelta(minutes=30)).timestamp()), "open": 1.5, "high": 2, "low": 1, "close": 1.7, "tick_volume": 11},
        {"time": int(NOW.timestamp()), "open": 1.7, "high": 2, "low": 1.6, "close": 1.8, "tick_volume": 2},
    ]
    with pytest.raises(ValueError, match="strictly ordered"):
        timeframe_snapshot("M15", raw)


def test_timeframe_adapter_keeps_newest_mt5_bar_as_current():
    raw = [
        {"time": int((NOW - timedelta(minutes=30)).timestamp()), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "tick_volume": 10},
        {"time": int((NOW - timedelta(minutes=15)).timestamp()), "open": 1.5, "high": 2, "low": 1, "close": 1.7, "tick_volume": 11},
        {"time": int(NOW.timestamp()), "open": 1.7, "high": 2, "low": 1.6, "close": 1.8, "tick_volume": 2},
    ]
    tf = timeframe_snapshot("M15", raw)
    assert len(tf.closed_bars) == 2
    assert tf.current_bar is not None and tf.current_bar.time_utc == NOW


def _profile():
    return RiskProfile(
        max_risk_per_trade_pct=D("1"), max_total_open_risk_pct=D("3"), daily_loss_limit_pct=D("3"),
        weekly_loss_limit_pct=D("5"), max_active_orders=3, min_margin_reserve_pct=D("0"),
    )


def _account():
    return AccountSnapshot(login=1, server="demo", currency="USD", balance=10000, equity=10000, margin=0, margin_free=10000, leverage=100, captured_at_utc=NOW)


def _contract():
    return SymbolContract(symbol="EURUSD", digits=5, point=0.00001, trade_contract_size=100000, volume_min=0.01, volume_max=100, volume_step=0.01, trade_stops_level=10)


def _tick():
    return TickSnapshot(symbol="EURUSD", bid=1.0999, ask=1.1, time_msc=int(NOW.timestamp()*1000), captured_at_utc=NOW)


def _safety():
    return SafetySnapshot(account_fingerprint="a"*64, contracts_fingerprint="b"*64, reconciled=True, captured_at_utc=NOW)


def _calc_profit(side, symbol, volume, entry, stop):
    del side, symbol
    return (stop-entry) * D("100000") * volume


def _calc_margin(side, symbol, volume, entry):
    del side, symbol
    return entry * D("100000") * volume / D("100")


def _fixture_strategy(snapshot, config, now):
    evidence = DecisionEvidence(("FIXTURE",), {"symbol": snapshot.symbol})
    candidate = build_candidate(
        snapshot=snapshot, config=config, side="BUY", entry=snapshot.ask, stop_loss=snapshot.ask-0.01,
        take_profit=snapshot.ask+0.02, generated_at_utc=now, expires_at_utc=now+timedelta(minutes=10), evidence=evidence,
    )
    return StrategyResult(candidate, None, evidence)


def test_orchestrator_persists_candidate_and_reduced_risk_profile(tmp_path):
    db = tmp_path / "test.db"; initialize(db)
    cfg = StrategyConfig(StrategyVersion("fixture", "1"), {})
    market = MarketSnapshot("EURUSD", NOW, int(NOW.timestamp()*1000), 1.0999, 1.1, {})
    advisory = Advisory(
        candidate_id="placeholder", action=AdvisoryAction.REDUCE_RISK, risk_multiplier=0.5,
        evidence_id="e1", expires_at_utc=NOW+timedelta(minutes=5), model_fingerprint="m1", advisory_cost=0,
    )
    orchestrator = DecisionOrchestrator(db_path=db, risk_profile=_profile(), strategies=(StrategyBinding(_fixture_strategy, cfg),))

    holder = {}
    def advisory_for(candidate_id):
        holder["candidate_id"] = candidate_id
        return Advisory(**{**advisory.__dict__, "candidate_id": candidate_id})

    decisions = orchestrator.scan(
        market, account=_account(), contract=_contract(), tick=_tick(), safety=_safety(),
        risk_context=RiskContext(daily_reference_equity=D("10000"), weekly_reference_equity=D("10000")),
        calc_profit=_calc_profit, calc_margin=_calc_margin, now_utc=NOW, advisory_for=advisory_for,
    )
    decision = decisions[0]
    assert decision.risk_result is not None and decision.risk_result.approved
    assert decision.risk_result.normalized_volume == D("0.05")
    assert decision.risk_result.risk_profile_fingerprint != _profile().fingerprint
    with session(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM advisories_v1").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM risk_decisions_v1").fetchone()[0] == 1


def _approved_result():
    return BrokerRiskResult(
        candidate_id="candidate-x", side="BUY", approved=True, reason_codes=(), normalized_symbol="EURUSD",
        normalized_volume=D("0.01"), executable_entry=D("1.1"), stop_loss=D("1.09"), take_profit=D("1.12"),
        projected_loss_account_currency=D("10"), margin_required=D("11"), risk_profile_fingerprint="r"*64,
        safety_snapshot_fingerprint="s"*64, expires_at_utc=NOW+timedelta(minutes=5),
    )


def test_orchestrator_claims_one_same_scan_portfolio_slot(tmp_path):
    db = tmp_path / "test.db"; initialize(db)
    cfg_a = StrategyConfig(StrategyVersion("fixture-a", "1"), {})
    cfg_b = StrategyConfig(StrategyVersion("fixture-b", "1"), {})
    profile = _profile().model_copy(update={
        "max_total_open_risk_pct": D("1"),
        "max_active_orders": 1,
    })
    orchestrator = DecisionOrchestrator(
        db_path=db,
        risk_profile=profile,
        strategies=(StrategyBinding(_fixture_strategy, cfg_a), StrategyBinding(_fixture_strategy, cfg_b)),
    )
    market = MarketSnapshot("EURUSD", NOW, int(NOW.timestamp()*1000), 1.0999, 1.1, {})
    decisions = orchestrator.scan(
        market, account=_account(), contract=_contract(), tick=_tick(), safety=_safety(),
        risk_context=RiskContext(daily_reference_equity=D("10000"), weekly_reference_equity=D("10000")),
        calc_profit=_calc_profit, calc_margin=_calc_margin, now_utc=NOW,
    )
    assert len(decisions) == 2
    assert decisions[0].risk_result is not None and decisions[0].risk_result.approved
    assert decisions[1].risk_result is not None and not decisions[1].risk_result.approved
    assert "MAX_ACTIVE_ORDERS" in decisions[1].risk_result.reason_codes
    assert "PORTFOLIO_SLOT_CLAIMED" in decisions[1].risk_result.reason_codes
    with session(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM risk_decisions_v1").fetchone()[0] == 2


def test_execution_service_requires_enabled_armed_kill_switch_clear_and_blocks_unknown(tmp_path):
    db = tmp_path / "test.db"; initialize(db)
    with pytest.raises(ExecutionDisarmed, match="execution_enabled=false"):
        GuardedExecutionService(db_path=db, execution_enabled=False).create_intent(_approved_result(), now_utc=NOW)

    service = GuardedExecutionService(db_path=db, execution_enabled=True, identity_guard=lambda: None)
    with pytest.raises(ExecutionDisarmed):
        service.create_intent(_approved_result(), now_utc=NOW)

    save_trading_control(db, TradingControlState(True, NOW+timedelta(hours=1), False, False, "MANUAL_ARM"))
    intent = service.create_intent(_approved_result(), now_utc=NOW)
    assert intent.state is ExecutionState.RISK_APPROVED

    unknown = OrderIntent(
        intent_id="unknown-1", candidate_id="other", idempotency_key="other-key", symbol="EURUSD", side="BUY",
        volume=D("0.01"), entry=D("1.1"), stop_loss=D("1.09"), take_profit=D("1.12"),
        state=ExecutionState.UNKNOWN, created_at_utc=NOW,
    )
    service.repository.save(unknown)
    other = BrokerRiskResult(**{**_approved_result().__dict__, "candidate_id": "candidate-y", "risk_profile_fingerprint": "q"*64})
    with pytest.raises(ExecutionDisarmed, match="reconciliation"):
        service.create_intent(other, now_utc=NOW)


def test_runtime_composition_builds_disarmed_services(tmp_path):
    cfg = RuntimeConfig(
        mode="SHADOW", symbols=("EURUSD",), db_path=tmp_path/"runtime.db", log_dir=tmp_path/"logs",
        poll_seconds=5, mt5_host="127.0.0.1", mt5_port=18812, mt5_ui_host="127.0.0.1", mt5_ui_port=8080,
        mt5_engine="docker",
    )
    services = build_integration_services(cfg)
    assert services.decisions.risk_profile.max_active_orders == 1
    assert not services.execution.execution_enabled
    assert load_trading_control(cfg.db_path).kill_switch


def test_repository_risk_profile_loader_uses_explicit_profile():
    loaded = load_risk_profile()
    assert loaded.max_risk_per_trade_pct == D("1")
    assert loaded.max_total_open_risk_pct == D("1")
    assert loaded.daily_loss_limit_pct == D("3")
    assert loaded.weekly_loss_limit_pct == D("5")
    assert loaded.max_active_orders == 1
