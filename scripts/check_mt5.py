#!/usr/bin/env python3
from __future__ import annotations

import json

from forex_ai.config import load_runtime_config
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol


def compact_account(account: dict | None) -> dict:
    if not account:
        return {}
    keys = [
        "login", "server", "currency", "balance", "equity", "margin",
        "margin_free", "margin_level", "trade_allowed", "trade_expert",
    ]
    return {key: account.get(key) for key in keys if key in account}


def main() -> int:
    cfg = load_runtime_config()
    client = MT5Client(cfg)
    print(f"Forex-AI mode: {cfg.mode}")
    print(f"MT5 bridge: {cfg.mt5_host}:{cfg.mt5_port}")
    try:
        ok = client.connect()
        print(f"initialize: {ok}")
        print("version:", client.version())
        terminal = client.terminal_info()
        print("terminal:", json.dumps(terminal or {}, ensure_ascii=False, default=str))
        account = client.account_info()
        print("account:", json.dumps(compact_account(account), ensure_ascii=False, default=str))

        if not account:
            print("STATUS=WAITING_FOR_MT5_LOGIN")
            return 2

        available = client.symbols()
        mapping: dict[str, str | None] = {}
        for base in cfg.symbols:
            actual = resolve_symbol(base, available)
            mapping[base] = actual
            if actual:
                info = client.symbol_info(actual) or {}
                tick = client.tick(actual) or {}
                summary = {
                    "actual": actual,
                    "digits": info.get("digits"),
                    "point": info.get("point"),
                    "volume_min": info.get("volume_min"),
                    "volume_max": info.get("volume_max"),
                    "volume_step": info.get("volume_step"),
                    "trade_stops_level": info.get("trade_stops_level"),
                    "bid": tick.get("bid"),
                    "ask": tick.get("ask"),
                }
                print(f"symbol[{base}]:", json.dumps(summary, ensure_ascii=False, default=str))
            else:
                print(f"symbol[{base}]: NOT_FOUND")

        print("positions:", len(client.positions()))
        print("mapping:", json.dumps(mapping, ensure_ascii=False))
        print("STATUS=READY_READ_ONLY")
        return 0
    except Exception as exc:
        print(f"STATUS=ERROR {type(exc).__name__}: {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
