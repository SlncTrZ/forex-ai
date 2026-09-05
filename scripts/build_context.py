#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from forex_ai.config import load_runtime_config
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.mt5.client import MT5Client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", choices=["XAUUSD", "EURUSD"])
    args = parser.parse_args()

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    try:
        if not client.connect():
            print("MT5 initialize failed")
            return 2
        context = build_symbol_context(client, cfg, args.symbol)
        print(json.dumps(context, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
