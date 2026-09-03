from __future__ import annotations
from datetime import datetime, timezone
from forex_ai.research.monte_carlo import bootstrap_trade_monte_carlo
from forex_ai.research.replay import ReplayTrade
UTC=timezone.utc

def trade(i:int,r:float)->ReplayTrade:
    t=datetime(2026,1,1,tzinfo=UTC)
    return ReplayTrade(str(i),'s','EURUSD','BUY',t,t,1,1,0.9,1.1,r,r,r,'TEST',0)

def test_monte_carlo_is_deterministic_and_reports_positive_probability():
    rows=(trade(1,1.0),trade(2,1.0),trade(3,-0.5),trade(4,-0.5))
    a=bootstrap_trade_monte_carlo(rows,samples=500,seed=7)
    b=bootstrap_trade_monte_carlo(rows,samples=500,seed=7)
    assert a==b
    assert a.trade_count==4
    assert 0<a.probability_positive_expectancy<=1
    assert a.max_drawdown_r_p95>=a.max_drawdown_r_median>=0
