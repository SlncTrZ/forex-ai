#!/usr/bin/env python3
"""Bounded independent BTC/XAU observation; no scanner/executor invocation."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from forex_ai.advisory.ollama_safety_gate import D3DecisionSafetyGate, GateUnavailable
from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize
from forex_ai.market.intelligence import observe_symbol, observe_unavailable
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol_strict

UTC = timezone.utc
DEFAULT_MONITORS = ("XAUUSDm", "BTCUSDm")


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    monitors = tuple(x.strip() for x in os.getenv("FOREX_AI_MONITOR_SYMBOLS", ",".join(DEFAULT_MONITORS)).split(",") if x.strip())
    if not monitors or len(monitors) > 6 or any(not s.isalnum() or len(s) > 32 for s in monitors):
        raise ValueError("INVALID_MONITOR_SYMBOLS")
    now = datetime.now(UTC)
    try:
        provider = D3DecisionSafetyGate.from_environment()
    except GateUnavailable:
        provider = None
    client = MT5Client(cfg)
    statuses: list[dict] = []
    try:
        try:
            connected = client.connect()
        except Exception:
            connected = False
        if not connected:
            for name in monitors:
                observe_unavailable(name, reason="OBSERVATION_FAILED", db_path=cfg.db_path, now_utc=now)
                statuses.append({"symbol": name, "reason_code": "OBSERVATION_FAILED"})
            return 1
        try:
            available = client.symbol_candidates(monitors)
            h1 = client.constants()["H1"]
        except Exception:
            for name in monitors:
                observe_unavailable(name, reason="OBSERVATION_FAILED", db_path=cfg.db_path, now_utc=now)
                statuses.append({"symbol": name, "reason_code": "OBSERVATION_FAILED"})
            return 1
        for name in monitors:
            actual = resolve_symbol_strict(name, available)
            if actual is None:
                observe_unavailable(name, reason="SYMBOL_UNRESOLVED", db_path=cfg.db_path, now_utc=now)
                statuses.append({"symbol": name, "reason_code": "SYMBOL_UNRESOLVED"})
                continue
            try:
                rows = client.bars(actual, h1, 271)
                if not rows:
                    observe_unavailable(actual, reason="MT5_BARS_UNAVAILABLE", db_path=cfg.db_path, now_utc=now)
                    statuses.append({"symbol": actual, "reason_code": "MT5_BARS_UNAVAILABLE"})
                    continue
                tick = client.tick(actual)
                result = observe_symbol(actual, rows=rows, tick=tick, provider=provider, db_path=cfg.db_path,
                                        now_utc=datetime.now(UTC))
                statuses.append({"symbol": actual, "reason_code": result["reason_code"],
                                 "market_state": result["market_state"]})
            except Exception:
                observe_unavailable(actual, reason="OBSERVATION_FAILED", db_path=cfg.db_path, now_utc=datetime.now(UTC))
                statuses.append({"symbol": actual, "reason_code": "OBSERVATION_FAILED"})
    finally:
        client.close()
        print(json.dumps({"component": "market_intelligence", "symbols": statuses}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
