#!/usr/bin/env python3
from __future__ import annotations

import time
from datetime import timedelta

from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize, log_event
from forex_ai.journal.repository import insert_position_snapshots, upsert_mt5_deals, upsert_mt5_orders
from forex_ai.mt5.client import MT5Client


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    client = MT5Client(cfg)
    try:
        if not client.connect():
            log_event(cfg.db_path, "ERROR", "history_sync", "mt5_initialize_failed", {})
            return 2
        end_ts = time.time() + 60
        start_ts = end_ts - timedelta(days=90).total_seconds()
        deals = client.history_deals(start_ts, end_ts)
        orders = client.history_orders(start_ts, end_ts)
        positions = client.positions()
        deal_count = upsert_mt5_deals(cfg.db_path, deals)
        order_count = upsert_mt5_orders(cfg.db_path, orders)
        position_count = insert_position_snapshots(cfg.db_path, positions)
        log_event(
            cfg.db_path,
            "INFO",
            "history_sync",
            "sync_ok",
            {"deals": deal_count, "orders": order_count, "positions": position_count},
        )
        print(f"Synced deals={deal_count} orders={order_count} positions={position_count}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
