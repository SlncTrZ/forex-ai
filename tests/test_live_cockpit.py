"""Live cockpit: broker financial truth, stale guards, monitoring isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from forex_ai.dashboard.live_account import LiveAccountCache, broker_financial_snapshot
from forex_ai.dashboard.ticks import _resolve_symbols
from forex_ai.runtime.market_schedule import new_entries_allowed

UTC = timezone.utc


def test_financial_truth_from_mt5_excludes_funding_and_never_sends(monkeypatch):
    from forex_ai.dashboard import live_account
    monkeypatch.setattr(live_account, "account_matches", lambda info: True)
    monkeypatch.setattr(live_account, "detect_account_trade_mode",
                        lambda info: SimpleNamespace(name="DEMO"))
    class Fake:
        def account_info(self):
            return {"balance": 1000, "equity": 1012.5, "profit": 12.5,
                    "margin": 20, "margin_free": 992.5, "currency": "USD"}
        def history_deals(self, start, end):
            assert end > start
            return [
                {"symbol": "BTCUSDm", "profit": 9, "commission": -2, "fee": 0, "swap": -1},
                {"symbol": "", "profit": 5000},  # deposit is NOT trading PnL
            ]
        def positions(self): return [{"profit": 12.5}]
        def order_send(self, *_): raise AssertionError("never execute")
    out = broker_financial_snapshot(Fake(), datetime(2026, 10, 31, 8, tzinfo=UTC))
    assert out["equity"] == 1012.5
    assert out["floating_pnl"] == 12.5
    assert out["realized_pnl_utc_day"] == 6
    assert out["trade_deals_utc_day"] == 1
    assert out["source"] == "MT5_DIRECT"


def test_live_cache_expired_hides_financial_values():
    c = LiveAccountCache()
    assert c.snapshot()["state"] == "UNAVAILABLE"
    with c.lock:
        c.latest = {
            "observed_at": (datetime.now(UTC)-timedelta(minutes=2)).isoformat(),
            "balance": 1000, "equity": 1000,
        }
    stale = c.snapshot()
    assert stale["state"] == "STALE"
    assert "equity" not in stale and "balance" not in stale


def test_btc_market_monitor_is_separate_from_execution_symbols():
    class Fake:
        def symbol_candidates(self, requested):
            assert requested == ("XAUUSD", "BTCUSD")
            return [{"name": "XAUUSDm"}, {"name": "BTCUSDm"}, {"name": "BTCUSDTm"}]
    config = SimpleNamespace(symbols=("XAUUSD",), db_path=SimpleNamespace(is_file=lambda: False))
    out = _resolve_symbols(Fake(), config)
    assert out == ("XAUUSDm", "BTCUSDm")
    assert config.symbols == ("XAUUSD",)
    assert new_entries_allowed(datetime(2026, 10, 31, 8, tzinfo=UTC), "BTCUSDm")
    assert not new_entries_allowed(datetime(2026, 10, 31, 8, tzinfo=UTC), "XAUUSDm")


def test_dashboard_never_reports_stale_zero_equity_as_live(tmp_path, monkeypatch):
    from forex_ai.dashboard import server
    from forex_ai.journal.db import initialize
    db = tmp_path/"equity.db"; initialize(db)
    monkeypatch.setattr(server,"load_runtime_config",lambda:SimpleNamespace(db_path=db))
    monkeypatch.setattr(server,"load_execution_enabled",lambda:False)
    monkeypatch.setattr(server,"load_trading_control",lambda _:SimpleNamespace(
        armed=False,kill_switch=False,maintenance_mode=False,arm_expires_at_utc=None,
        reason="TEST",allows_new_entries=lambda **kw:False))
    monkeypatch.setattr(server,"assess_runtime_health",lambda *args,**kw:SimpleNamespace(
        healthy=True,reasons=(),latest_heartbeat_state="HEALTHY",
        latest_heartbeat_age_seconds=1.0,db_integrity="ok",free_bytes=10000000000,
        unresolved_execution_intents=0))
    monkeypatch.setattr(server,"read_services",lambda:[
        {"name":"candidate-scan","unit":"forex-ai-candidate-scan.service","status":"inactive"}])
    monkeypatch.setattr(server,"_risk_summary",lambda:{})
    monkeypatch.setattr(server,"_release_info",lambda:{"version":"v0.0.5"})
    monkeypatch.setattr(server.LIVE_ACCOUNT,"snapshot",lambda:{"state":"STALE","source":"MT5_DIRECT"})
    old=server.read_status()
    assert old["account"]["financials"] is None
    assert old["health"]["healthy"] is False
    assert "BROKER_EQUITY_STALE" in old["health"]["reasons"]
    monkeypatch.setattr(server.LIVE_ACCOUNT,"snapshot",lambda:{
        "state":"LIVE","source":"MT5_DIRECT","mode":"DEMO","bound":True,
        "age_seconds":1.2,"balance":1000.0,"equity":1000.0,"margin":0.0,
        "free_margin":1000.0,"currency":"USD","floating_pnl":0.0,
        "realized_pnl_utc_day":0.0
    })
    live=server.read_status()
    assert live["account"]["financials"]["equity"]==1000
    assert live["account"]["financial_source"]=="MT5_DIRECT"
    assert live["health"]["healthy"] is True
    assert live["infrastructure"]=="RUNNING"
    assert live["metrics"]["realized_pnl_utc_day"]==0.0

def test_legacy_strategy_profile_missing_btc_defaults_disabled(tmp_path):
    import yaml
    from forex_ai.config import PROJECT_ROOT
    from forex_ai.strategy.config import load_strategy_snapshot
    source = yaml.safe_load((PROJECT_ROOT/'config'/'strategy.yaml').read_text())
    source['strategies'].pop('btc_squeeze_breakout_m15')
    legacy = tmp_path/'strategy.yaml'
    legacy.write_text(yaml.safe_dump(source))
    snapshot = load_strategy_snapshot(legacy, allow_last_good=False)
    assert snapshot.enabled('btc_squeeze_breakout_m15') is False
    assert snapshot.enabled('supertrend_volume_m5') == bool(source['strategies']['supertrend_volume_m5']['enabled'])
    source['strategies'].pop('supertrend_volume_m5')
    legacy.write_text(yaml.safe_dump(source))
    import pytest
    with pytest.raises(ValueError, match='missing strategy ids'):
        load_strategy_snapshot(legacy, allow_last_good=False)
