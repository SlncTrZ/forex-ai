#!/usr/bin/env python3
from __future__ import annotations

from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize, log_event
from forex_ai.journal.repository import insert_account, insert_market_snapshot
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    client = MT5Client(cfg)
    try:
        if not client.connect():
            log_event(cfg.db_path, "WARN", "collector", "mt5_initialize_failed", {})
            return 2
        account = client.account_info()
        if not account:
            log_event(cfg.db_path, "WARN", "collector", "account_unavailable", {})
            return 3
        account_id = insert_account(cfg.db_path, account)
        available = client.symbols()
        mapping = {base: resolve_symbol(base, available) for base in cfg.symbols}
        constants = client.constants()
        for base, actual in mapping.items():
            if not actual:
                log_event(cfg.db_path, "WARN", "collector", "symbol_unresolved", {"base": base})
                continue
            tick = client.tick(actual) or {}
            bars = client.bars(actual, constants["M15"], 120)
            insert_market_snapshot(
                cfg.db_path,
                actual,
                tick,
                {"base_symbol": base, "timeframe": "M15", "bars": bars},
            )
        log_event(cfg.db_path, "INFO", "collector", "collect_once_ok", {"account_snapshot_id": account_id, "mapping": mapping})
        print(f"Collected account + {sum(v is not None for v in mapping.values())} symbols into {cfg.db_path}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
