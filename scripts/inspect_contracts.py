#!/usr/bin/env python3
from forex_ai.config import load_runtime_config
from forex_ai.mt5.client import MT5Client

ACCOUNT_KEYS = [
    "trade_mode", "leverage", "margin_mode", "currency", "balance", "equity",
    "trade_allowed", "trade_expert", "server",
]
SYMBOL_KEYS = [
    "digits", "point", "spread", "spread_float", "trade_calc_mode", "trade_mode",
    "trade_exemode", "trade_contract_size", "trade_tick_size", "trade_tick_value",
    "trade_tick_value_profit", "trade_tick_value_loss", "volume_min", "volume_max",
    "volume_step", "currency_base", "currency_profit", "currency_margin", "swap_mode",
    "swap_long", "swap_short", "trade_stops_level", "trade_freeze_level",
    "filling_mode", "order_mode",
]


def main() -> None:
    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        raise SystemExit("MT5 initialize failed")
    try:
        account = client.account_info() or {}
        print("ACCOUNT", {k: account.get(k) for k in ACCOUNT_KEYS})
        available = {item.get("name") for item in client.symbols()}
        for base in cfg.symbols:
            actual = next((name for name in sorted(available) if name and name.upper().startswith(base.upper())), None)
            if not actual:
                print(base, "NOT_FOUND")
                continue
            info = client.symbol_info(actual) or {}
            tick = client.tick(actual) or {}
            out = {k: info.get(k) for k in SYMBOL_KEYS}
            out.update({"bid": tick.get("bid"), "ask": tick.get("ask")})
            print(actual, out)
    finally:
        client.close()


if __name__ == "__main__":
    main()
