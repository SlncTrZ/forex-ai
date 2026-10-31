"""Confluence scoring keeps D3 ordinal grades bounded by source evidence."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from forex_ai.advisory.confluence_scoring import assess_confluence, _d3_score
from forex_ai.advisory.regime_router import RegimeDecision
from forex_ai.advisory.ollama_safety_gate import GateUnavailable
from forex_ai.integration.engine import DecisionOrchestrator, StrategyBinding
from forex_ai.journal.db import initialize, session
from forex_ai.risk.broker_engine import RiskContext
from forex_ai.strategy.v1.contracts import (
    DecisionEvidence, MarketSnapshot, StrategyConfig, StrategyResult,
    StrategyVersion, build_candidate,
)
from test_integration_phase import (
    NOW, _account, _contract, _tick, _safety, _profile, _calc_profit, _calc_margin,
)


def regime():
    return RegimeDecision(
        "EURUSD", "UPTREND", "UPTREND", "UPTREND", .96, "REGIME_CONFIRMED",
        NOW.isoformat(), "f"*64, "m"*16, 400, atr_h1=.02,
    )


def candidate_result(*, with_evidence=True):
    m = MarketSnapshot("EURUSD", NOW, int(NOW.timestamp()*1000), 1.0999, 1.1, {})
    c = StrategyConfig(StrategyVersion("supertrend_volume_m5", "1"), {})
    vals = {"st_value": 1.095, "vol_ratio": 1.4, "trigger": "PULLBACK_BOUNCE"} if with_evidence else {}
    ev = DecisionEvidence(("SUPERTREND_VOLUME_CONFIRMED",) if with_evidence else (), vals)
    candidate = build_candidate(
        snapshot=m, config=c, side="BUY", entry=1.1, stop_loss=1.09,
        take_profit=1.12, generated_at_utc=NOW, expires_at_utc=NOW+timedelta(minutes=5),
        evidence=ev,
    )
    return StrategyResult(candidate, None, ev)


class FakeD3:
    fingerprint = "synthetic-d3"

    def __init__(self, grade=2, *, bad=False):
        self.grade, self.bad = grade, bad
        self.calls = 0

    def decide_questions(self, *, state, questions):
        self.calls += 1
        assert "password" not in state and "account_login" not in state
        assert set(questions) == {"value","momentum","trigger"}
        grade = self.grade
        probs = (
            {"0": 0., "1": 0., "2": 1.} if grade == 2 else
            {"0": 0., "1": .8, "2": .2} if grade == 1.2 else
            {"0": 1., "1": 0., "2": 0.}
        )
        ans = {key: {"type":"score", "score":grade, "confidence":.12,
                      "probabilities":probs} for key in questions}
        if self.bad:
            ans["trigger"]["score"] = 4
        return {"answers": ans}


@pytest.mark.parametrize(("grade","verdict","multiplier"), [
    (2, "FULL", Decimal(1)),
    (1.2, "REDUCE", Decimal(".5")),
    (0, "VETO", Decimal(0)),
])
def test_effective_confluence_score_and_persistent_cache(tmp_path, grade, verdict, multiplier):
    db = tmp_path/"score.db"
    initialize(db)
    provider = FakeD3(grade)
    result = candidate_result()
    first = assess_confluence(result, regime(), db_path=db, provider=provider)
    second = assess_confluence(result, regime(), db_path=db, provider=provider)
    assert first == second and first.verdict == verdict
    assert first.multiplier == multiplier and 0 <= first.score <= 100
    assert provider.calls == 1
    with session(db) as con:
        assert con.execute(
            "SELECT count(*) FROM audit_events WHERE event_type='D3_CONFLUENCE_DECISION'"
        ).fetchone()[0] == 1


def test_missing_source_evidence_cannot_be_made_up_by_d3(tmp_path):
    db = tmp_path/"score.db"; initialize(db)
    decision = assess_confluence(candidate_result(with_evidence=False), regime(),
                                db_path=db, provider=FakeD3(2))
    assert decision.score == 30
    assert decision.value_points == 0 and decision.momentum_points == 0
    assert decision.trigger_points == 0 and not decision.allowed


def test_unavailable_or_invalid_d3_vetoes_and_no_risk_is_assessed(tmp_path):
    db = tmp_path/"score.db"; initialize(db)
    decision = assess_confluence(candidate_result(), regime(), db_path=db, provider=None)
    assert not decision.allowed and decision.reason_code == "CONFLUENCE_D3_UNAVAILABLE"
    bad = assess_confluence(candidate_result(), regime(), db_path=db, provider=FakeD3(bad=True))
    assert not bad.allowed and bad.reason_code == "CONFLUENCE_D3_UNAVAILABLE"


def test_scoring_response_schema_rejects_inconsistent_and_nan():
    with pytest.raises(GateUnavailable):
        _d3_score({"type":"score","score":1.9,"confidence":.9,
                   "probabilities":{"0":.2,"1":.6,"2":.2}})
    with pytest.raises(GateUnavailable):
        _d3_score({"type":"score","score":float("nan"),"confidence":.9,
                   "probabilities":{"0":0,"1":1,"2":0}})


def test_orchestrator_confluence_reduces_before_risk_engine(tmp_path):
    db=tmp_path/"test.db"; initialize(db)
    result=candidate_result()
    cfg=StrategyConfig(StrategyVersion("supertrend_volume_m5","1"),{})
    engine=DecisionOrchestrator(db_path=db,risk_profile=_profile(),
        strategies=(StrategyBinding(lambda *_:result,cfg),))
    m=MarketSnapshot("EURUSD", NOW, int(NOW.timestamp()*1000),1.0999,1.1,{})
    decision=engine.scan(m,
        account=_account(), contract=_contract(), tick=_tick(), safety=_safety(),
        risk_context=RiskContext(daily_reference_equity=Decimal("10000"),
                                 weekly_reference_equity=Decimal("10000")),
        calc_profit=_calc_profit,calc_margin=_calc_margin,now_utc=NOW,
        regime_decision=regime(),
        confluence_for=lambda r, g:assess_confluence(
            r,g,db_path=db,provider=FakeD3(1.2)),
    )[0]
    assert decision.confluence is not None
    assert decision.confluence.verdict=="REDUCE"
    assert decision.risk_result is not None and decision.risk_result.approved
    assert decision.risk_result.risk_profile_fingerprint != _profile().fingerprint
