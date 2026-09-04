#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import asdict

from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches
from forex_ai.config import load_runtime_config
from forex_ai.execution.demo_campaign import assess_demo_campaign_readiness


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
    report = assess_demo_campaign_readiness(
        db_path=cfg.db_path,
        mode=cfg.mode,
        execution_enabled=os.getenv("FOREX_AI_EXECUTION_ENABLED", "false").lower() in {"1", "true", "yes"},
        campaign_id=os.getenv("FOREX_AI_DEMO_CAMPAIGN_ID", ""),
        account_trade_mode=account_trade_mode,
        account_identity_bound=account_identity_bound,
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
