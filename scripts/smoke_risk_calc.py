#!/usr/bin/env python3
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_ai.config import load_risk_profile, load_runtime_config
from forex_ai.integration.adapters import account_snapshot, symbol_contract, tick_snapshot
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.contracts import SafetySnapshot
from forex_ai.mt5.symbols import resolve_symbol_strict
from forex_ai.risk.broker_engine import BrokerAwareRiskEngine, CandidateInput, RiskContext

D = Decimal
UTC = timezone.utc


def main() -> int:
    cfg = load_runtime_config()
    profile = load_risk_profile()
    client = MT5Client(cfg)
    if not client.connect():
        print("connect=false")
        return 2
    try:
        now = datetime.now(UTC)
        raw_account = client.account_info()
        if not raw_account:
            print("account=false")
            return 3
        account = account_snapshot(raw_account, captured_at_utc=now)
        available = client.symbols()
        constants = client.constants()
        order_types = {"BUY": constants["ORDER_TYPE_BUY"], "SELL": constants["ORDER_TYPE_SELL"]}
        failures = 0
        for base in cfg.symbols:
            actual = resolve_symbol_strict(base, available)
            if actual is None:
                print(f"{base}:mapping=FAIL")
                failures += 1
                continue
            info = client.symbol_info(actual)
            raw_tick = client.tick(actual)
            if not info or not raw_tick:
                print(f"{base}:market_data=FAIL")
                failures += 1
                continue
            tick = tick_snapshot(raw_tick, symbol=actual, captured_at_utc=now)
            trade_mode = int(info.get("trade_mode") or 0)
            order_mode = int(info.get("order_mode") or 0)
            trade_allowed = trade_mode != int(constants["SYMBOL_TRADE_MODE_DISABLED"])
            market_allowed = bool(order_mode & int(constants["SYMBOL_ORDER_MARKET"]))
            contract = symbol_contract(
                info,
                symbol=actual,
                trade_allowed=trade_allowed,
                market_orders_allowed=market_allowed,
                session_open=trade_allowed and market_allowed,
            )
            point = D(str(contract.point))
            minimum_distance_points = max(contract.trade_stops_level, contract.trade_freeze_level, 100) + 10
            distance = D(minimum_distance_points) * point
            entry = D(str(tick.ask))
            stop = entry - distance
            target = entry + distance * D("2")
            candidate = CandidateInput(
                candidate_id=f"smoke:{base}",
                symbol=actual,
                side="BUY",
                reference_entry=entry,
                stop_loss=stop,
                take_profit=target,
                expires_at_utc=now + timedelta(minutes=5),
            )
            safety = SafetySnapshot(
                account_fingerprint=account.identity_fingerprint,
                contracts_fingerprint=contract.contract_fingerprint,
                reconciled=True,
                captured_at_utc=now,
            )
            context = RiskContext(
                daily_reference_equity=D(str(account.equity)),
                weekly_reference_equity=D(str(account.equity)),
            )

            def calc_profit(side: str, symbol: str, volume: D, open_price: D, close_price: D) -> D:
                value = client.order_calc_profit(
                    order_types[side], symbol, float(volume), float(open_price), float(close_price)
                )
                if value is None:
                    raise RuntimeError("order_calc_profit returned None")
                return D(str(value))

            def calc_margin(side: str, symbol: str, volume: D, open_price: D) -> D:
                value = client.order_calc_margin(order_types[side], symbol, float(volume), float(open_price))
                if value is None:
                    raise RuntimeError("order_calc_margin returned None")
                return D(str(value))

            min_volume = D(str(contract.volume_min))
            min_loss = abs(calc_profit("BUY", actual, min_volume, entry, stop))
            min_margin = calc_margin("BUY", actual, min_volume, entry)
            risk_budget = D(str(account.equity)) * profile.max_risk_per_trade_pct / D("100")
            raw_volume = risk_budget / (min_loss / min_volume)
            engine = BrokerAwareRiskEngine(profile)
            floored_volume = engine._floor_volume(raw_volume, contract)
            floored_loss = abs(calc_profit("BUY", actual, floored_volume, entry, stop)) if floored_volume > 0 else D("0")
            result = engine.evaluate(
                candidate,
                account=account,
                contract=contract,
                tick=tick,
                safety=safety,
                context=context,
                calc_profit=calc_profit,
                calc_margin=calc_margin,
                now_utc=now,
            )
            print(
                f"{base}:symbol={actual} min_lot={contract.volume_min} max_lot={contract.volume_max} step={contract.volume_step} "
                f"min_loss={min_loss:.8f} min_margin={min_margin:.8f} risk_budget={risk_budget:.8f} "
                f"raw_volume={raw_volume:.8f} floored_volume={floored_volume} floored_loss={floored_loss:.8f} "
                f"approved={result.approved} volume={result.normalized_volume} "
                f"reasons={','.join(result.reason_codes) or 'NONE'}"
            )
            if "INVALID_PROFIT_CALC" in result.reason_codes or "INVALID_MARGIN_CALC" in result.reason_codes:
                failures += 1
        return 0 if failures == 0 else 4
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
