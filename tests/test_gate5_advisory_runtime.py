from __future__ import annotations

from datetime import datetime, timezone

from forex_ai.advisory.models import AdvisoryStatus, ProviderResult
from forex_ai.advisory.provider import BudgetPolicy, CircuitBreakerPolicy
from forex_ai.advisory.runtime import AdvisoryRuntime, AdvisoryRuntimePolicy
from forex_ai.strategy.v1.contracts import CandidateEnvelope

UTC=timezone.utc
NOW=datetime(2026,9,3,12,0,tzinfo=UTC)


def candidate(i:int)->CandidateEnvelope:
    return CandidateEnvelope(str(i),str(i),'s','1','EURUSD','BUY',1.0,0.9,1.2,NOW,1,NOW.replace(hour=13),'e','m')


class Provider:
    provider_id='p'
    def __init__(self):self.calls=0;self.fail=False;self.short=False
    def review(self,candidates,macro_context,now_utc):
        self.calls+=1
        if self.fail:raise TimeoutError('timeout')
        rows=[ProviderResult(AdvisoryStatus.AVAILABLE,None,model_fingerprint='m',cost=0.01) for _ in candidates]
        return rows[:-1] if self.short else rows


def runtime(provider=None,max_calls=2):
    p=provider or Provider()
    return AdvisoryRuntime(p,'m',AdvisoryRuntimePolicy(BudgetPolicy(max_calls,1000,1.0),cache_ttl_seconds=300,circuit_breaker=CircuitBreakerPolicy(2,60))),p


def test_cache_avoids_repeat_provider_call():
    rt,p=runtime();c=(candidate(1),)
    a=rt.review_batch(c,macro_context={},macro_cache_key='k',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=10,estimated_cost=.1)
    b=rt.review_batch(c,macro_context={},macro_cache_key='k',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=10,estimated_cost=.1)
    assert a==b and p.calls==1


def test_budget_exhaustion_returns_bot_only_compatible_unavailable():
    rt,p=runtime(max_calls=1)
    a=rt.review_batch((candidate(1),),macro_context={},macro_cache_key='a',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=10,estimated_cost=.1)
    b=rt.review_batch((candidate(2),),macro_context={},macro_cache_key='b',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=10,estimated_cost=.1)
    assert a[0].status is AdvisoryStatus.AVAILABLE
    assert b[0].status is AdvisoryStatus.UNAVAILABLE and b[0].error=='ADVISORY_BUDGET_EXHAUSTED'
    assert p.calls==1


def test_provider_failures_open_circuit_and_never_raise():
    p=Provider();p.fail=True;rt,_=runtime(p)
    first=rt.review_batch((candidate(1),),macro_context={},macro_cache_key='1',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=1,estimated_cost=0)
    second=rt.review_batch((candidate(2),),macro_context={},macro_cache_key='2',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=1,estimated_cost=0)
    third=rt.review_batch((candidate(3),),macro_context={},macro_cache_key='3',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=1,estimated_cost=0)
    assert first[0].status is AdvisoryStatus.UNAVAILABLE
    assert second[0].status is AdvisoryStatus.UNAVAILABLE
    assert third[0].error=='ADVISORY_CIRCUIT_OPEN'
    assert p.calls==2


def test_cardinality_mismatch_falls_back_closed_to_no_change_path():
    p=Provider();p.short=True;rt,_=runtime(p)
    out=rt.review_batch((candidate(1),candidate(2)),macro_context={},macro_cache_key='k',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=1,estimated_cost=0)
    assert len(out)==2 and all(x.status is AdvisoryStatus.UNAVAILABLE for x in out)
    assert all(x.error=='ADVISORY_PROVIDER_CARDINALITY' for x in out)


def test_batch_limit_blocks_provider_call():
    rt,p=runtime();rt.policy=AdvisoryRuntimePolicy(BudgetPolicy(10,1000,1),max_batch_candidates=1)
    out=rt.review_batch((candidate(1),candidate(2)),macro_context={},macro_cache_key='k',now_utc=NOW,config_fingerprint='cfg',estimated_tokens=1,estimated_cost=0)
    assert p.calls==0 and all(x.error=='ADVISORY_BATCH_LIMIT' for x in out)
