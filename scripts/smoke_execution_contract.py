#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_ai.config import load_runtime_config
from forex_ai.execution.mt5 import MT5MarketRequestPolicy, build_market_request
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.integration.adapters import symbol_contract
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol_strict

D = Decimal
UTC = timezone.utc


def main() -> int:
    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        print("connect=false")
        return 2
    try:
        available = client.symbols()
        basic = client.constants()
        execution = client.execution_constants()
        failures = 0
        for base in cfg.symbols:
            actual = resolve_symbol_strict(base, available)
            if actual is None:
                print(f"{base}:mapping=FAIL")
                failures += 1
                continue
            info = client.symbol_info(actual)
            tick = client.tick(actual)
            if not info or not tick:
                print(f"{base}:market_data=FAIL")
                failures += 1
                continue
            trade_mode = int(info.get("trade_mode") or 0)
            order_mode = int(info.get("order_mode") or 0)
            contract = symbol_contract(
                info,
                symbol=actual,
                trade_allowed=trade_mode != int(basic["SYMBOL_TRADE_MODE_DISABLED"]),
                market_orders_allowed=bool(order_mode & int(basic["SYMBOL_ORDER_MARKET"])),
                session_open=True,
            )
            point = D(str(contract.point))
            price_step = D(str(contract.trade_tick_size or contract.point))
            entry = D(str(tick["ask"]))
            distance_points = max(contract.trade_stops_level, contract.trade_freeze_level, 100) + 10
            minimum_distance = point * D(distance_points)
            price_steps = (minimum_distance / price_step).to_integral_value(rounding=ROUND_CEILING)
            distance = price_step * price_steps
            item = OrderIntent(
                intent_id=f"smoke-{base.lower()}",
                candidate_id=f"smoke-candidate-{base.lower()}",
                idempotency_key=f"smoke-key-{base.lower()}",
                symbol=actual,
                side="BUY",
                volume=D(str(contract.volume_min)),
                entry=entry,
                stop_loss=entry - distance,
                take_profit=entry + distance * D("2"),
                state=ExecutionState.RISK_APPROVED,
                created_at_utc=datetime.now(UTC),
            )
            request = build_market_request(
                item,
                contract=contract,
                constants=execution,
                policy=MT5MarketRequestPolicy(deviation_points=20, magic=0),
            )
            print(
                f"{base}:symbol={actual} point={contract.point} tick_size={contract.trade_tick_size} filling_flags={contract.filling_mode} "
                f"request_filling={request['type_filling']} action={request['action']} "
                f"order_type={request['type']} volume={request['volume']} build=PASS"
            )
        return 0 if failures == 0 else 3
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
