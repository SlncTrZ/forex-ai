#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches
from forex_ai.config import load_risk_profile, load_runtime_config
from forex_ai.execution.live_canary import assess_live_canary_readiness


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
    report = assess_live_canary_readiness(
        db_path=cfg.db_path,
        mode=cfg.mode,
        execution_enabled=os.getenv("FOREX_AI_EXECUTION_ENABLED", "false").lower() in {"1", "true", "yes"},
        symbols=cfg.symbols,
        risk_profile=load_risk_profile(),
        approval_path=Path(os.environ["FOREX_AI_STRATEGY_APPROVAL_FILE"]).expanduser() if os.getenv("FOREX_AI_STRATEGY_APPROVAL_FILE") else None,
        account_trade_mode=account_trade_mode,
        account_identity_bound=account_identity_bound,
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.ready else 4


if __name__ == "__main__":
    raise SystemExit(main())
