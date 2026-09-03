#!/usr/bin/env python3
from __future__ import annotations

import argparse, json
from dataclasses import asdict
from pathlib import Path

from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.research.monte_carlo import bootstrap_trade_monte_carlo
from forex_ai.research.oos import OOSAcceptancePolicy, assess_oos, run_walk_forward_fold, split_events
from forex_ai.research.replay import CostModel, ReplayEngine
from forex_ai.research.sensitivity import cost_sensitivity
from forex_ai.research.walkforward import Fold
from forex_ai.strategy.v1 import trend_pullback, volatility_breakout


def _strategy(name: str):
    if name == "trend_pullback": return trend_pullback.evaluate, trend_pullback.DEFAULT_CONFIG
    if name == "volatility_breakout": return volatility_breakout.evaluate, volatility_breakout.DEFAULT_CONFIG
    raise ValueError(name)


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument('--dataset',required=True); p.add_argument('--strategy',choices=('trend_pullback','volatility_breakout'),required=True); p.add_argument('--output',required=True); p.add_argument('--point',type=float,default=0.00001); a=p.parse_args()
    ds=load_frozen_replay_dataset(Path(a.dataset)); events=ds.events; n=len(events)
    if n < 100: raise RuntimeError('INSUFFICIENT_DATASET_EVENTS')
    i60=max(1,int(n*0.60)); i80=max(i60+1,int(n*0.80))
    fold=Fold(events[0].clock_utc,events[i60].clock_utc,events[i60].clock_utc,events[i80].clock_utc,events[i80].clock_utc,events[-1].clock_utc)
    strategy,config=_strategy(a.strategy)
    baseline=CostModel(spread=0.0,commission_per_trade=0.0,slippage=0.0,swap_per_day=0.0,reject_probability=0.0)
    evidence=run_walk_forward_fold(ds,fold=fold,engine=ReplayEngine(strategy,config,baseline),account_r_value=1.0)
    policy=OOSAcceptancePolicy(min_test_trades=30,min_expectancy_r=0.0,require_positive_ci_lower_bound=True)
    acceptance=assess_oos(evidence,policy)
    test_events=split_events(events,fold,'test')
    models=(
        baseline,
        CostModel(slippage=a.point),
        CostModel(slippage=3*a.point,reject_probability=0.01),
        CostModel(slippage=5*a.point,reject_probability=0.03),
    )
    sensitivity=cost_sensitivity(strategy=strategy,config=config,events=test_events,cost_models=models)
    mc=bootstrap_trade_monte_carlo(evidence.test.replay_artifact.trades,samples=5000)
    report={
      'dataset_manifest':ds.manifest.as_dict(),'dataset_manifest_fingerprint':ds.manifest.fingerprint,'strategy':a.strategy,
      'strategy_config_fingerprint':config.fingerprint,'fold':asdict(fold),'evidence_fingerprint':evidence.fingerprint,
      'train':{'events':evidence.train.event_count,'candidates':evidence.train.replay_artifact.candidate_count,'metrics':asdict(evidence.train.metrics)},
      'validation':{'events':evidence.validation.event_count,'candidates':evidence.validation.replay_artifact.candidate_count,'metrics':asdict(evidence.validation.metrics)},
      'test':{'events':evidence.test.event_count,'candidates':evidence.test.replay_artifact.candidate_count,'metrics':asdict(evidence.test.metrics)},
      'acceptance_policy':asdict(policy),'acceptance':asdict(acceptance),
      'sensitivity':[{'cost_model':asdict(model),'candidate_count':point.candidate_count,'rejected_count':point.rejected_count,'metrics':asdict(point.metrics)} for model,point in zip(models,sensitivity)],
      'monte_carlo':asdict(mc),
    }
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n',encoding='utf-8')
    print(json.dumps({'strategy':a.strategy,'train':report['train']['metrics'],'validation':report['validation']['metrics'],'test':report['test']['metrics'],'acceptance':report['acceptance'],'monte_carlo':report['monte_carlo']},indent=2,default=str))
    return 0

if __name__=='__main__':raise SystemExit(main())
