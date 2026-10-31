#!/usr/bin/env python3
"""Verify actual MT5 account and REAL approval without changing trading state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.execution.account_mode import detect_account_trade_mode
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches
from forex_ai.release_preflight import assess_release_account
from forex_ai.execution.auto_week import AutoWeekApproval
from forex_ai.strategy.config import bundled_strategy_snapshot


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval", type=Path, required=True)
    args = parser.parse_args()

    client = MT5Client(load_runtime_config())
    mode = "UNKNOWN"
    bound = False
    try:
        if not client.connect():
            print(json.dumps({"permitted": False, "reason_code": "RELEASE_BROKER_UNAVAILABLE"}))
            return 3
        raw = client.account_info() or {}
        mode = detect_account_trade_mode(raw).name
        bound = account_matches(raw)
    except Exception:
        print(json.dumps({"permitted": False, "reason_code": "RELEASE_BROKER_UNAVAILABLE"}))
        return 3
    finally:
        client.close()

    approved = matched = False
    if mode == "REAL" and bound and args.approval.is_file():
        try:
            approval = AutoWeekApproval.load(args.approval)
            approved = approval.strategy_approval.approved
            matched = (
                approval.strategy_approval.strategy_config_fingerprint
                == bundled_strategy_snapshot().production_fingerprint
            )
        except (ValueError, OSError, KeyError, TypeError):
            approved = matched = False

    result = assess_release_account(
        mode=mode, bound=bound, real_approval_present=args.approval.is_file(),
        real_approval_valid=approved, real_fingerprint_matches=matched,
    )
    print(json.dumps({"permitted": result.permitted,
                      "reason_code": result.reason_code, "mode": result.mode}))
    return 0 if result.permitted else 3


if __name__ == "__main__":
    raise SystemExit(main())
