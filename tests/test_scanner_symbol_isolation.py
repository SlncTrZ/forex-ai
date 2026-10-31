"""Scanner sync errors are symbol-local; protected execution path remains untouched."""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from forex_ai.journal.db import initialize


def _scanner(monkeypatch):
    # The test exercises coordinator orchestration without an external RPyC broker.
    monkeypatch.setitem(sys.modules, "rpyc", types.ModuleType("rpyc"))
    target = Path(__file__).resolve().parents[1] / "scripts" / "scan_candidates.py"
    spec = importlib.util.spec_from_file_location("test_scan_symbols", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_xau_closed_cannot_abort_btc_candidate_scan(tmp_path, monkeypatch, capsys):
    scanner = _scanner(monkeypatch)
    db = tmp_path / "scan.db"
    cfg = SimpleNamespace(db_path=db, symbols=("XAUUSDm", "BTCUSDm"))
    monkeypatch.setattr(scanner, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(scanner, "assess_runtime_health", lambda _: SimpleNamespace(healthy=True))
    snapshot = SimpleNamespace(loaded_from_last_good=False)
    monkeypatch.setattr(scanner, "load_strategy_snapshot", lambda: snapshot)
    monkeypatch.setattr(scanner, "load_market_context_snapshot", lambda: snapshot)
    monkeypatch.setattr(scanner, "load_risk_profile", lambda: object())
    monkeypatch.setattr(scanner, "production_strategy_bindings", lambda _: ())
    monkeypatch.setattr(scanner, "required_raw_bars", lambda *a, **k: 261)
    monkeypatch.setattr(scanner, "DecisionOrchestrator", lambda **kw: object())

    scanned, closed = [], []

    class Client:
        def __init__(self, cfg): pass
        def constants(self): return {"H1": 16385}
    class Coordinator:
        def __init__(self, *, symbols, **kw): self.symbol = symbols[0]
        def sync_once(self, **kw):
            if self.symbol == "XAUUSDm":
                return SimpleNamespace(ready=False, reason="STALE_TICK:XAUUSDm")
            return SimpleNamespace(ready=True, symbol_mapping={"BTCUSDm": "BTCUSDm"})
        def close(self): closed.append(self.symbol)
    monkeypatch.setattr(scanner, "MT5Client", Client)
    monkeypatch.setattr(scanner, "MT5ResyncCoordinator", Coordinator)

    def scan(**kwargs):
        scanned.append(kwargs["base_symbol"])
        return [{"status": "no_signal"}]
    monkeypatch.setattr(scanner, "_scan_symbol", scan)
    assert scanner.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "partial"
    assert [r["status"] for r in result["results"]] == ["sync_blocked", "scanned"]
    assert scanned == ["BTCUSDm"]
    assert closed == ["XAUUSDm", "BTCUSDm"]


def test_strategy_binding_routes_only_to_eligible_symbols():
    from forex_ai.integration.engine import strategy_eligible_for_symbol
    assert strategy_eligible_for_symbol("btc_squeeze_breakout_m15", "BTCUSDm")
    assert not strategy_eligible_for_symbol("supertrend_volume_m5", "BTCUSDm")
    assert not strategy_eligible_for_symbol("btc_squeeze_breakout_m15", "XAUUSDm")
    assert strategy_eligible_for_symbol("supertrend_volume_m5", "XAUUSDm")
    assert not strategy_eligible_for_symbol("supertrend_volume_m5", "UNKNOWN")
    assert not strategy_eligible_for_symbol("btc_squeeze_breakout_m15", "BTCUSDT")


def test_outcomes_are_paired_only_with_symbol_eligible_bindings(monkeypatch):
    scanner = _scanner(monkeypatch)
    def binding(strategy_id):
        return SimpleNamespace(config=SimpleNamespace(version=SimpleNamespace(strategy_id=strategy_id)))
    bindings = (binding("supertrend_volume_m5"),
                binding("btc_squeeze_breakout_m15"),
                binding("smc_pro_fvg_v2"))
    btc = list(scanner._paired_eligible_decisions(("BTC_DECISION",), bindings, "BTCUSDm"))
    xau = list(scanner._paired_eligible_decisions(("XAU_A", "XAU_B"), bindings, "XAUUSDm"))
    assert [(d, b.config.version.strategy_id) for d, b in btc] == [
        ("BTC_DECISION", "btc_squeeze_breakout_m15")]
    assert [(d, b.config.version.strategy_id) for d, b in xau] == [
        ("XAU_A", "supertrend_volume_m5"), ("XAU_B", "smc_pro_fvg_v2")]


def test_advisory_policy_requires_authoritative_broker_demo(monkeypatch):
    scanner = _scanner(monkeypatch)
    cfg = SimpleNamespace(mode="DEMO")
    assert scanner._btc_demo_account_verified(cfg, {"trade_mode": 0})
    assert not scanner._btc_demo_account_verified(cfg, {"trade_mode": 1})
    assert not scanner._btc_demo_account_verified(cfg, {"trade_mode": 2})
    assert not scanner._btc_demo_account_verified(cfg, {})
    assert not scanner._btc_demo_account_verified(SimpleNamespace(mode="REAL"), {"trade_mode": 0})
    assert not scanner._btc_demo_account_verified(SimpleNamespace(mode="OBSERVE"), {"trade_mode": 0})
