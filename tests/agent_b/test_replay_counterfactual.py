from datetime import datetime, timedelta, timezone

from forex_ai.research.counterfactual import CounterfactualRecord, evaluate as evaluate_counterfactual
from forex_ai.research.evaluation import evaluate_trades
from forex_ai.research.replay import CostModel, ReplayEngine, ReplayEvent
from forex_ai.research.walkforward import rolling_folds
from forex_ai.strategy.v1.contracts import Candle, DecisionEvidence, MarketSnapshot, StrategyConfig, StrategyResult, StrategyVersion, TimeframeSnapshot, build_candidate

UTC=timezone.utc


def _strategy(snapshot, config, now):
    tf=snapshot.timeframes['M15']
    if len(tf.closed_bars)<2:
        return StrategyResult(None,None,DecisionEvidence(('NO',),{}),('NO',))
    entry=snapshot.ask; stop=entry-1; target=entry+2
    evidence=DecisionEvidence(('FIXTURE',),{'last':tf.closed_bars[-1].close})
    candidate=build_candidate(snapshot=snapshot,config=config,side='BUY',entry=entry,stop_loss=stop,take_profit=target,
                              generated_at_utc=now,expires_at_utc=now+timedelta(hours=1),evidence=evidence)
    return StrategyResult(candidate,None,evidence)


def _snap(now, bars):
    return MarketSnapshot('TEST',now,int(now.timestamp()*1000),100,100,{'M15':TimeframeSnapshot.from_sequence('M15',bars)})


def test_replay_uses_supplied_strategy_and_applies_costs():
    cfg=StrategyConfig(StrategyVersion('fixture','1'),{})
    t0=datetime(2026,1,1,tzinfo=UTC)
    b1=Candle(t0,100,100.2,99.8,100)
    b2=Candle(t0+timedelta(minutes=15),100,100.2,99.8,100)
    b3=Candle(t0+timedelta(minutes=30),100,102.5,99.9,102)
    events=[ReplayEvent(t0,_snap(t0,(b1,b2))),ReplayEvent(t0+timedelta(minutes=30),_snap(t0+timedelta(minutes=30),(b1,b2,b3)))]
    artifact=ReplayEngine(_strategy,cfg,CostModel(commission_per_trade=0.1)).run(events,account_r_value=10)
    assert artifact.candidate_count>=1
    assert len(artifact.trades)==1
    assert artifact.trades[0].gross_r==2.0
    assert artifact.trades[0].net_r<artifact.trades[0].gross_r
    metrics=evaluate_trades(artifact.trades)
    assert metrics.trade_count==1 and metrics.expectancy_r==artifact.trades[0].net_r


def test_walkforward_boundaries_do_not_overlap():
    base=datetime(2026,1,1,tzinfo=UTC)
    points=[base+timedelta(days=i*30) for i in range(5)]
    folds=rolling_folds(points)
    assert folds[0].train_end==folds[0].validation_start
    assert folds[0].validation_end==folds[0].test_start
    assert folds[0].train_start<folds[0].train_end<=folds[0].validation_start<folds[0].validation_end<=folds[0].test_start


def test_counterfactual_counts_false_veto_and_incremental_value():
    rows=[
        CounterfactualRecord('a','X','s','VETO',0,1,0,2,2,'m',10,0.01,'conflict'),
        CounterfactualRecord('b','X','s','REDUCE_RISK',0.5,1,0,2,-1,'m',10,0.01,'risk'),
        CounterfactualRecord('c','X','s','NO_CHANGE',1,1,0,2,1,'m',10,0.01,''),
    ]
    report=evaluate_counterfactual(rows,api_cost_to_r=1.0)
    assert report.technical_candidate_count==3
    assert report.veto_count==1 and report.reduction_count==1
    assert report.false_veto_rate==1.0
    assert report.api_cost_total==0.03
    assert report.net_incremental_value_r<report.incremental_expectancy_r
