#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import asdict

from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches
from forex_ai.config import load_execution_enabled, load_runtime_config
from forex_ai.execution.demo_readiness import assess_demo_readiness


def main() -> int:
    cfg = load_runtime_config()
    client = MT5Client(cfg)
    account_trade_mode = None
    account_identity_bound = False
    if client.connect():
        try:
            account = client.account_info() or {}
            account_trade_mode = account.get("trade_mode")
            account_identity_bound = account_matches(account)
        finally:
            client.close()
    report = assess_demo_readiness(
        db_path=cfg.db_path,
        mode="DEMO",
        execution_enabled=load_execution_enabled(),
        account_trade_mode=account_trade_mode,
        account_identity_bound=account_identity_bound,
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
