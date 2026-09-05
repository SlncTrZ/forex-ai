from __future__ import annotations

from datetime import datetime, timedelta, timezone

from forex_ai.strategy.v1.live_scan import build_market_from_mt5_rows, evaluate_v1_market, scan_result_payload

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def _rows(step: int, count: int, start_price: float = 1.10):
    rows=[]
    for i in range(count):
        p=start_price+i*0.0001
        rows.append({"time":int((START+timedelta(seconds=step*i)).timestamp()),"open":p,"high":p+0.0002,"low":p-0.0002,"close":p+0.0001,"tick_volume":100+i})
    return rows


def test_live_market_excludes_current_forming_bar():
    bars={"M5":_rows(300,60),"M15":_rows(900,60),"H1":_rows(3600,60),"H4":_rows(14400,60)}
    market=build_market_from_mt5_rows(symbol="EURUSDc",tick_raw={"bid":1.2,"ask":1.2001,"time_msc":int((START+timedelta(days=20)).timestamp()*1000)},bars_by_timeframe=bars,captured_at_utc=START+timedelta(days=20))
    for name,rows in bars.items():
        assert len(market.timeframes[name].closed_bars)==len(rows)-1
        assert market.timeframes[name].current_bar is not None
        assert market.timeframes[name].current_bar.time_utc == datetime.fromtimestamp(rows[-1]["time"],UTC)


def test_v1_scan_returns_frozen_prospective_strategy_verdicts_with_reason_payload():
    bars={"M5":_rows(300,80),"M15":_rows(900,80),"H1":_rows(3600,80),"H4":_rows(14400,80)}
    market=build_market_from_mt5_rows(symbol="EURUSDc",tick_raw={"bid":1.2,"ask":1.2001,"time_msc":int((START+timedelta(days=20)).timestamp()*1000)},bars_by_timeframe=bars,captured_at_utc=START+timedelta(days=20))
    results=evaluate_v1_market(market,now_utc=START+timedelta(days=20))
    assert {r.strategy_id for r in results}=={
        "inside_bar_momentum_breakout_v1",
        "breakout_retest_v1",
        "trend_pullback_v1",
    }
    for row in results:
        payload=scan_result_payload(row)
        assert payload["strategy_id"]==row.strategy_id
        assert "reason_codes" in payload
        assert "values" in payload["evidence"]
