from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forex_ai.advisory.budget_store import SQLiteDailyBudgetStore
from forex_ai.advisory.models import AdvisoryStatus, ProviderResult
from forex_ai.advisory.provider import BudgetPolicy
from forex_ai.advisory.runtime import AdvisoryRuntime, AdvisoryRuntimePolicy
from forex_ai.config import load_risk_config
from forex_ai.intelligence.schemas import CurrentContextCheck, ReviewDecision
from forex_ai.journal.db import initialize, session
from forex_ai.journal.integration_repository import persist_candidate
from forex_ai.risk.account_guard import AccountBindingError, assert_account_matches, bind_account
from forex_ai.strategy.v1.contracts import (
    Candle,
    CandidateEnvelope,
    DecisionEvidence,
    MarketSnapshot,
    StrategyConfig,
    StrategyVersion,
    TimeframeSnapshot,
    build_candidate,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)


class _Provider:
    provider_id = "test-provider"

    def __init__(self):
        self.calls = 0

    def review(self, candidates, macro_context, now_utc):
        del macro_context, now_utc
        self.calls += 1
        return tuple(
            ProviderResult(AdvisoryStatus.AVAILABLE, None, model_fingerprint="model", cost=0.01)
            for _ in candidates
        )


def _candidate(candidate_id: str) -> CandidateEnvelope:
    return CandidateEnvelope(
        candidate_id=candidate_id,
        correlation_id=f"corr-{candidate_id}",
        strategy_id="fixture",
        strategy_version="1",
        symbol="EURUSD",
        side="BUY",
        reference_entry=1.1,
        stop_loss=1.09,
        take_profit=1.12,
        generated_at_utc=NOW,
        market_time_msc=int(NOW.timestamp() * 1000),
        expires_at_utc=NOW + timedelta(hours=1),
        evidence_hash="e" * 64,
        market_snapshot_fingerprint="m" * 64,
        opportunity_key=f"opp-{candidate_id}",
    )


def test_account_binding_is_persistent_and_fail_closed(tmp_path):
    path = tmp_path / "account_fingerprint"
    account = {"login": 123, "server": "broker-demo", "currency": "USD"}
    with pytest.raises(AccountBindingError, match="ACCOUNT_BINDING_MISSING"):
        assert_account_matches(account, path=path, require_binding=True)

    bound = bind_account(account, path)
    assert len(bound) == 64
    assert assert_account_matches(account, path=path, require_binding=True) == bound

    with pytest.raises(AccountBindingError, match="ACCOUNT_IDENTITY_MISMATCH"):
        assert_account_matches({**account, "login": 456}, path=path, require_binding=True)

    path.write_text("not-a-fingerprint\n", encoding="utf-8")
    with pytest.raises(AccountBindingError, match="ACCOUNT_BINDING_INVALID"):
        assert_account_matches(account, path=path, require_binding=True)


def test_opportunity_and_candidate_identity_ignore_volatile_retry_tick(tmp_path):
    closed = (
        Candle(NOW - timedelta(minutes=30), 1.09, 1.11, 1.08, 1.10, 10),
        Candle(NOW - timedelta(minutes=15), 1.10, 1.12, 1.09, 1.11, 11),
    )
    tf = TimeframeSnapshot("M15", closed)
    cfg = StrategyConfig(StrategyVersion("fixture", "1"), {"x": 1})
    evidence = DecisionEvidence(("SETUP",), {"x": 1})
    market_a = MarketSnapshot("EURUSD", NOW, int(NOW.timestamp() * 1000), 1.1099, 1.1100, {"M15": tf})
    market_b = MarketSnapshot(
        "EURUSD",
        NOW + timedelta(seconds=4),
        int((NOW + timedelta(seconds=4)).timestamp() * 1000),
        1.1101,
        1.1102,
        {"M15": tf},
    )
    a = build_candidate(
        snapshot=market_a,
        config=cfg,
        side="BUY",
        entry=1.1100,
        stop_loss=1.1000,
        take_profit=1.1300,
        generated_at_utc=NOW,
        expires_at_utc=NOW + timedelta(minutes=5),
        evidence=evidence,
    )
    b = build_candidate(
        snapshot=market_b,
        config=cfg,
        side="BUY",
        entry=1.1102,
        stop_loss=1.1002,
        take_profit=1.1302,
        generated_at_utc=NOW + timedelta(seconds=4),
        expires_at_utc=NOW + timedelta(minutes=5, seconds=4),
        evidence=evidence,
    )
    assert a.opportunity_key == b.opportunity_key
    assert a.candidate_id == b.candidate_id
    assert a.market_snapshot_fingerprint != b.market_snapshot_fingerprint

    db = tmp_path / "opportunity.db"
    initialize(db)
    persist_candidate(db, a)
    persist_candidate(db, b)
    with session(db) as con:
        assert con.execute("SELECT count(*) FROM candidate_decisions").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM strategy_opportunities_v1").fetchone()[0] == 1


def test_daily_advisory_budget_survives_process_reconstruction(tmp_path):
    db = tmp_path / "budget.db"
    initialize(db)
    store = SQLiteDailyBudgetStore(db, "test-provider", "model", "cfg")
    first_provider = _Provider()
    first = AdvisoryRuntime(
        first_provider,
        "model",
        AdvisoryRuntimePolicy(BudgetPolicy(1, 1000, 1.0)),
        budget_store=store,
    )
    out = first.review_batch(
        (_candidate("a"),),
        macro_context={},
        macro_cache_key="a",
        now_utc=NOW,
        config_fingerprint="cfg",
        estimated_tokens=10,
        estimated_cost=0.01,
    )
    assert out[0].status is AdvisoryStatus.AVAILABLE
    assert first_provider.calls == 1

    second_provider = _Provider()
    second = AdvisoryRuntime(
        second_provider,
        "model",
        AdvisoryRuntimePolicy(BudgetPolicy(1, 1000, 1.0)),
        budget_store=store,
    )
    blocked = second.review_batch(
        (_candidate("b"),),
        macro_context={},
        macro_cache_key="b",
        now_utc=NOW + timedelta(minutes=1),
        config_fingerprint="cfg",
        estimated_tokens=10,
        estimated_cost=0.01,
    )
    assert blocked[0].error == "ADVISORY_BUDGET_EXHAUSTED"
    assert second_provider.calls == 0


def test_review_schema_rejects_unverified_structure_and_extra_fields():
    with pytest.raises(ValueError):
        CurrentContextCheck(topic="rates", status="VERIFIED", finding="x", sources=[])
    with pytest.raises(ValueError):
        ReviewDecision(
            action="NO_TRADE",
            confidence=0.5,
            thesis="x",
            invalidation="y",
            web_search_used=False,
            current_context_checks=[
                {"topic": "rates", "status": "VERIFIED", "finding": "x", "sources": ["https://example.com"]}
            ],
        )
    with pytest.raises(ValueError):
        ReviewDecision(action="NO_TRADE", confidence=0.5, thesis="x", invalidation="y", unexpected=True)


def test_production_risk_config_has_single_profile_authority():
    raw = load_risk_config()
    assert "profile" in raw
    assert "limits" not in raw


def test_production_source_does_not_import_legacy_risk_engine():
    root = Path(__file__).resolve().parents[1] / "src" / "forex_ai"
    offenders = []
    for path in root.rglob("*.py"):
        if path.as_posix().endswith("/risk/engine.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "forex_ai.risk.engine" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
