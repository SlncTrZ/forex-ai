from __future__ import annotations

import signal
import time
from dataclasses import dataclass

from forex_ai.config import RuntimeConfig
from forex_ai.journal.db import initialize, log_event
from forex_ai.journal.deal_audit import audit_mt5_deals
from forex_ai.journal.repository import (
    insert_account,
    insert_market_snapshot,
    insert_position_snapshots,
    upsert_mt5_deals,
    upsert_mt5_orders,
)
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol


@dataclass
class ObserverState:
    stop: bool = False


def run_observer(cfg: RuntimeConfig) -> int:
    """Read-only live journal loop. This module contains no order path."""
    if cfg.mode != "OBSERVE":
        raise RuntimeError(f"Observer requires OBSERVE mode, got {cfg.mode}")

    initialize(cfg.db_path)
    state = ObserverState()

    def request_stop(signum, _frame):
        state.stop = True
        log_event(cfg.db_path, "INFO", "observer", "stop_requested", {"signal": signum})

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    client = MT5Client(cfg)
    try:
        if not client.connect():
            log_event(cfg.db_path, "ERROR", "observer", "mt5_initialize_failed", {"error": client.last_error()})
            return 2

        account = client.account_info()
        if not account:
            log_event(cfg.db_path, "WARN", "observer", "account_unavailable", {})
            return 3

        available = client.symbols()
        mapping = {base: resolve_symbol(base, available) for base in cfg.symbols}
        unresolved = [base for base, actual in mapping.items() if actual is None]
        if unresolved:
            log_event(cfg.db_path, "ERROR", "observer", "symbol_mapping_failed", {"unresolved": unresolved})
            return 4

        log_event(cfg.db_path, "INFO", "observer", "observer_started", {"mapping": mapping})
        timeframes = client.constants()
        last_account_at = 0.0
        last_bars_at = 0.0
        last_history_at = 0.0
        history_bootstrap = True

        while not state.stop:
            now = time.monotonic()
            if now - last_account_at >= 60:
                account = client.account_info()
                if account:
                    insert_account(cfg.db_path, account)
                last_account_at = now

            positions = client.positions()
            insert_position_snapshots(cfg.db_path, positions)

            if now - last_history_at >= 60:
                end_ts = time.time() + 60
                lookback_seconds = 90 * 86400 if history_bootstrap else 2 * 86400
                start_ts = end_ts - lookback_seconds
                deals = client.history_deals(start_ts, end_ts)
                upsert_mt5_deals(cfg.db_path, deals)
                audit_mt5_deals(cfg.db_path, deals)
                upsert_mt5_orders(cfg.db_path, client.history_orders(start_ts, end_ts))
                history_bootstrap = False
                last_history_at = now

            include_bars = now - last_bars_at >= 300
            for base, actual in mapping.items():
                assert actual is not None
                tick = client.tick(actual) or {}
                payload = {"kind": "tick", "base_symbol": base, "timeframe": None}
                if include_bars:
                    payload["M15"] = client.bars(actual, timeframes["M15"], 200)
                    payload["H1"] = client.bars(actual, timeframes["H1"], 200)
                insert_market_snapshot(cfg.db_path, actual, tick, payload)

            if include_bars:
                last_bars_at = now

            time.sleep(max(1, cfg.poll_seconds))

        log_event(cfg.db_path, "INFO", "observer", "observer_stopped", {})
        return 0
    finally:
        client.close()
