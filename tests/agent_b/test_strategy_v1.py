from datetime import datetime, timedelta, timezone

import pytest

from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, StrategyConfig, TimeframeSnapshot
from forex_ai.strategy.v1.trend_pullback import DEFAULT_CONFIG as PULLBACK_CONFIG, evaluate as eval_pullback
from forex_ai.strategy.v1.volatility_breakout import DEFAULT_CONFIG as BREAKOUT_CONFIG, evaluate as eval_breakout

UTC = timezone.utc


def _trend_bars(start: datetime, count: int, step: float = 0.1, base: float = 100.0):
    bars=[]
    price=base
    for i in range(count):
        o=price; c=price+step; h=max(o,c)+0.08; l=min(o,c)-0.08
        bars.append(Candle(start+timedelta(minutes=i),o,h,l,c,100+i))
        price=c
    return bars


def _snapshot(current_variant: float = 0.0):
    start=datetime(2026,1,1,tzinfo=UTC)
    h4=_trend_bars(start,60,0.25,100)
    h1=_trend_bars(start,60,0.18,100)
    m15=_trend_bars(start,60,0.12,100)
    prev=m15[-2]
    m15[-2]=Candle(prev.time_utc,prev.open,prev.high,prev.low-1.5,prev.close,prev.volume)
    last=m15[-1]
    m15[-1]=Candle(last.time_utc,last.open,last.high+0.2,last.low,last.close+0.15,last.volume)
    current=Candle(start+timedelta(minutes=999),999,1000+current_variant,998,999.5,1)
    return MarketSnapshot('TEST',start+timedelta(hours=20),1234567890000,108.0,108.02,{
        'H4':TimeframeSnapshot.from_sequence('H4',h4,current),
        'H1':TimeframeSnapshot.from_sequence('H1',h1,current),
        'M15':TimeframeSnapshot.from_sequence('M15',m15,current),
    },spread_cost=0.01)


def test_pullback_is_deterministic_and_current_bar_is_ignored():
    now=datetime(2026,1,2,tzinfo=UTC)
    a=eval_pullback(_snapshot(0),PULLBACK_CONFIG,now)
    b=eval_pullback(_snapshot(5000),PULLBACK_CONFIG,now)
    assert a.candidate is not None
    assert b.candidate is not None
    assert a.candidate == b.candidate
    assert a.candidate.candidate_id == b.candidate.candidate_id
    assert not hasattr(a.candidate,'volume')


def test_one_current_bar_produces_no_closed_signal():
    now=datetime(2026,1,2,tzinfo=UTC)
    current=Candle(now,100,101,99,100.5)
    snap=MarketSnapshot('TEST',now,1,100,100.1,{
        'H4':TimeframeSnapshot('H4',(),current),
        'H1':TimeframeSnapshot('H1',(),current),
        'M15':TimeframeSnapshot('M15',(),current),
    })
    result=eval_pullback(snap,PULLBACK_CONFIG,now)
    assert result.candidate is None
    assert 'INSUFFICIENT_CLOSED_BARS' in result.no_setup_reason_codes


def test_timeframe_rejects_duplicate_unordered_and_nonfinite_bars():
    start=datetime(2026,1,1,tzinfo=UTC)
    a=Candle(start,100,101,99,100)
    b=Candle(start+timedelta(minutes=15),100,101,99,100)
    with pytest.raises(ValueError):
        TimeframeSnapshot('M15',(a,a))
    with pytest.raises(ValueError):
        TimeframeSnapshot('M15',(b,a))
    with pytest.raises(ValueError):
        Candle(start,100,float('nan'),99,100)


def test_breakout_positive_and_cost_rejection():
    now=datetime(2026,1,2,tzinfo=UTC)
    start=now-timedelta(hours=20)
    bars=_trend_bars(start,60,0.04,100)
    prior_high=max(b.high for b in bars[-20:])
    p=bars[-1].close
    bars.append(Candle(now-timedelta(minutes=15),p,prior_high+0.15,p-0.25,prior_high+0.05,1000))
    snap=MarketSnapshot('TEST',now,2,prior_high+0.04,prior_high+0.05,{'M15':TimeframeSnapshot.from_sequence('M15',bars)},spread_cost=0.001)
    cfg=StrategyConfig(BREAKOUT_CONFIG.version,{**BREAKOUT_CONFIG.parameters,'min_expansion':1.0,'min_efficiency':0.1})
    ok=eval_breakout(snap,cfg,now)
    assert ok.candidate is not None
    expensive=MarketSnapshot('TEST',now,2,snap.bid,snap.ask,snap.timeframes,spread_cost=10.0)
    rejected=eval_breakout(expensive,cfg,now)
    assert rejected.candidate is None
    assert 'COST_TOO_HIGH' in rejected.no_setup_reason_codes
