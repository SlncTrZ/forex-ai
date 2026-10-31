"""Real worker entrypoint with a fake MT5 transport: no order execution."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from forex_ai.journal.db import initialize, session
from forex_ai.market.intelligence import EVENT


def _entry(monkeypatch):
    monkeypatch.setitem(sys.modules, "rpyc", types.ModuleType("rpyc"))
    path = Path(__file__).resolve().parents[1] / "scripts" / "observe_market_intelligence.py"
    spec = importlib.util.spec_from_file_location("tested_intel_entrypoint", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_worker_runs_independent_symbols_without_issuing_orders(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timezone, timedelta
    script = _entry(monkeypatch)
    db = tmp_path / "live-like.db"
    cfg = SimpleNamespace(db_path=db)
    monkeypatch.setattr(script, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(script.D3DecisionSafetyGate, "from_environment",
                        lambda: None)
    monkeypatch.setenv("FOREX_AI_MONITOR_SYMBOLS", "XAUUSDm,BTCUSDm")
    now = datetime.now(timezone.utc)
    first = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=270)
    def bars():
        out = []
        for i in range(271):
            c = 44000.0 - i * 8.0
            out.append({"time": int((first + timedelta(hours=i)).timestamp()),
                        "open": c+2, "high": c+3, "low": c-2,
                        "close": c, "tick_volume": 200})
        return out
    trace = []
    class FakeClient:
        def __init__(self, cfg): pass
        def connect(self): return True
        def symbol_candidates(self, names): return [{"name": x} for x in names]
        def constants(self): return {"H1": 16385}
        def bars(self, sym, tf, count):
            trace.append(("bars", sym))
            return bars()
        def tick(self, sym):
            trace.append(("tick", sym))
            return None if sym.startswith("XAU") else {
                "time_msc": int(datetime.now(timezone.utc).timestamp() * 1000),
                "bid": 40000, "ask": 40001}
        def close(self): trace.append(("close",))
        def order_send(self, *_): raise AssertionError("forbidden")
    monkeypatch.setattr(script, "MT5Client", FakeClient)
    assert script.main() == 0
    assert ("bars", "XAUUSDm") in trace
    assert ("bars", "BTCUSDm") in trace
    with session(db) as con:
        symbols = {row[0] for row in con.execute(
            "SELECT DISTINCT symbol FROM audit_events WHERE event_type=?", (EVENT,))}
    assert symbols == {"XAUUSDm", "BTCUSDm"}
    assert ("close",) in trace


def test_worker_connection_failure_audited_per_symbol(tmp_path, monkeypatch):
    script = _entry(monkeypatch)
    db = tmp_path / "failure.db"
    cfg = SimpleNamespace(db_path=db)
    monkeypatch.setattr(script, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(script.D3DecisionSafetyGate, "from_environment", lambda: None)
    monkeypatch.setenv("FOREX_AI_MONITOR_SYMBOLS", "XAUUSDm,BTCUSDm")
    class DeadClient:
        def __init__(self, cfg): pass
        def connect(self): raise OSError("bridge offline")
        def close(self): pass
    monkeypatch.setattr(script, "MT5Client", DeadClient)
    assert script.main() == 1
    with session(db) as con:
        rows = con.execute("SELECT symbol FROM audit_events WHERE event_type=?", (EVENT,)).fetchall()
    assert {row[0] for row in rows} == {"XAUUSDm", "BTCUSDm"}
