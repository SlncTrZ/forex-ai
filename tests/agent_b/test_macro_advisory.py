from datetime import datetime, timedelta, timezone

from forex_ai.advisory.models import AdvisoryAction, AdvisoryEvidence, AdvisoryStatus, ProviderResult
from forex_ai.advisory.policy import AdvisoryPolicy, apply_provider_result
from forex_ai.macro.cache import MacroSnapshotCache
from forex_ai.macro.models import FreshnessPolicy, MacroRegime, MacroSnapshot, ScheduledEvent, SourceEvidence, calendar_state

UTC=timezone.utc


def _macro(now, *, model='m1', source='s1'):
    ev=ScheduledEvent('nfp','NFP',now+timedelta(minutes=5),'HIGH',('USD',))
    src=SourceEvidence('official',now,'abc','PRIMARY')
    return MacroSnapshot('snap',now,MacroRegime.MIXED,(src,),(ev,),{'affected_assets':('USD',)},None,source,'input',model,'cfg')


def test_macro_cache_key_changes_and_blackout_unavailable():
    now=datetime(2026,1,1,tzinfo=UTC)
    a=_macro(now,model='m1'); b=_macro(now,model='m2')
    assert a.cache_key != b.cache_key
    policy=FreshnessPolicy(ttl_seconds=60,event_pre_seconds=600,event_post_seconds=60)
    assert calendar_state(a,now,policy)=='BLACKOUT'
    assert calendar_state(None,now,policy)=='UNAVAILABLE'
    cache=MacroSnapshotCache(); cache.put(a,now+timedelta(seconds=30)); assert cache.get(a.cache_key,now)==a
    assert cache.get(a.cache_key,now+timedelta(seconds=31)) is None


def test_advisory_cannot_increase_risk_and_unavailable_is_bot_only():
    now=datetime(2026,1,1,tzinfo=UTC); policy=AdvisoryPolicy()
    ev=AdvisoryEvidence('e','CONFLICT',True,True)
    reduced=apply_provider_result(candidate_id='c',result=ProviderResult(AdvisoryStatus.AVAILABLE,ev,AdvisoryAction.REDUCE_RISK,1.5,'m',0.01),now_utc=now,policy=policy)
    assert reduced.action==AdvisoryAction.NO_CHANGE and reduced.risk_multiplier==1.0
    unavailable=apply_provider_result(candidate_id='c',result=ProviderResult(AdvisoryStatus.UNAVAILABLE,None,error='timeout'),now_utc=now,policy=policy)
    assert unavailable.status==AdvisoryStatus.UNAVAILABLE
    assert unavailable.action==AdvisoryAction.NO_CHANGE
    assert unavailable.risk_multiplier==1.0


def test_veto_requires_source_backed_material_conflict():
    now=datetime(2026,1,1,tzinfo=UTC); policy=AdvisoryPolicy()
    weak=AdvisoryEvidence('e1','RUMOR',False,True)
    result=apply_provider_result(candidate_id='c',result=ProviderResult(AdvisoryStatus.AVAILABLE,weak,AdvisoryAction.VETO,0.0,'m'),now_utc=now,policy=policy)
    assert result.action==AdvisoryAction.NO_CHANGE
    strong=AdvisoryEvidence('e2','SCHEDULED_CONFLICT',True,True)
    veto=apply_provider_result(candidate_id='c',result=ProviderResult(AdvisoryStatus.AVAILABLE,strong,AdvisoryAction.VETO,0.7,'m'),now_utc=now,policy=policy)
    assert veto.action==AdvisoryAction.VETO and veto.risk_multiplier==0.0
