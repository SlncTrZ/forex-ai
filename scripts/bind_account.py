#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from forex_ai.config import load_runtime_config
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_fingerprint, bind_account


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview or explicitly bind the currently connected MT5 account identity."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Persist the current account fingerprint. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        print(json.dumps({"status": "mt5_initialize_failed"}))
        return 2
    try:
        account = client.account_info()
        if not account:
            print(json.dumps({"status": "account_unavailable"}))
            return 3
        fingerprint = account_fingerprint(account)
        if not args.confirm:
            print(json.dumps({
                "status": "preview",
                "fingerprint": fingerprint,
                "persisted": False,
                "instruction": "Re-run with --confirm only after verifying the intended broker account in MT5.",
            }))
            return 0
        persisted = bind_account(account)
        print(json.dumps({"status": "bound", "fingerprint": persisted, "persisted": True}))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
