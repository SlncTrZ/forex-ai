#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Ensure release src takes precedence over site-packages
RELEASE_SRC = Path(__file__).resolve().parent.parent / "src"
if RELEASE_SRC.is_dir() and str(RELEASE_SRC) not in sys.path:
    sys.path.insert(0, str(RELEASE_SRC))

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from forex_ai.config import load_execution_enabled, load_fixed_lot, load_risk_profile, load_runtime_config
from forex_ai.advisory.ollama_safety_gate import GateUnavailable, D3DecisionSafetyGate
from forex_ai.execution.account_mode import MT5AccountTradeMode, detect_account_trade_mode, require_account_trade_mode
from forex_ai.execution.live_canary import assess_live_canary_readiness
from forex_ai.execution.demo_readiness import assess_demo_readiness
from forex_ai.execution.mt5 import (
    MT5MarketRequestPolicy,
    MT5RetcodeClassifier,
    build_market_request,
    build_smart_trade_request,
    order_check_passed,
)
from forex_ai.integration.adapters import candidate_input
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.integration_repository import latest_approved_risk_result, load_candidate
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches, assert_account_matches
from forex_ai.risk.broker_engine import apply_fixed_volume, BrokerAwareRiskEngine
from forex_ai.runtime.resilience import MT5ResyncCoordinator
from forex_ai.runtime.risk_context import build_risk_context
from forex_ai.strategy.config import load_strategy_snapshot, required_raw_bars

UTC = timezone.utc
D = Decimal


def _policy() -> MT5MarketRequestPolicy:
    return MT5MarketRequestPolicy(
        deviation_points=int(os.getenv("FOREX_AI_DEVIATION_POINTS", "20")),
        magic=int(os.getenv("FOREX_AI_MAGIC", "260904")),
        comment_prefix="FXAI",
    )


def _readiness(cfg, *, account_trade_mode: MT5AccountTradeMode, account_identity_bound: bool,
               execution_enabled: bool, strategy_config_fingerprint: str):
    """Use the MT5 account type; cfg.mode must not grant execution authority."""
    now = datetime.now(UTC)
    if account_trade_mode is MT5AccountTradeMode.DEMO:
        return assess_demo_readiness(
            db_path=cfg.db_path,
            mode="DEMO",
            execution_enabled=execution_enabled,
            account_trade_mode=int(account_trade_mode),
            account_identity_bound=account_identity_bound,
            now_utc=now,
        )
    if account_trade_mode is MT5AccountTradeMode.REAL:
        approval = os.getenv("FOREX_AI_STRATEGY_APPROVAL_FILE")
        return assess_live_canary_readiness(
            db_path=cfg.db_path,
            mode="LIVE_CANARY",
            execution_enabled=execution_enabled,
            symbols=cfg.symbols,
            risk_profile=load_risk_profile(),
            strategy_config_fingerprint=strategy_config_fingerprint,
            approval_path=Path(approval).expanduser() if approval else None,
            account_trade_mode=int(account_trade_mode),
            account_identity_bound=account_identity_bound,
            now_utc=now,
        )
    raise RuntimeError(f"ACCOUNT_TRADE_MODE_NOT_SUPPORTED:{account_trade_mode.name}")


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    execution_enabled = load_execution_enabled()
    strategy_snapshot = load_strategy_snapshot()

    client = MT5Client(cfg)
    coordinator = MT5ResyncCoordinator(
        client=client,
        symbols=cfg.symbols,
        db_path=cfg.db_path,
        bars_count=max(80, required_raw_bars(strategy_snapshot, production_only=False)),
        load_history=True,
        history_refresh_seconds=0,
    )
    try:
        now = datetime.now(UTC)
        outcome = coordinator.sync_once(now_utc=now)
        if not outcome.ready or outcome.broker_state is None or outcome.safety is None or not outcome.raw_account:
            print(json.dumps({"status": "blocked", "reasons": [outcome.reason or "BROKER_STATE_NOT_READY"]}))
            return 4

        expected_mode = detect_account_trade_mode(outcome.raw_account)
        if expected_mode is MT5AccountTradeMode.CONTEST:
            print(json.dumps({"status": "blocked", "reasons": ["ACCOUNT_TRADE_MODE_NOT_SUPPORTED:CONTEST"]}))
            return 0
        readiness = _readiness(
            cfg,
            account_trade_mode=expected_mode,
            account_identity_bound=account_matches(outcome.raw_account),
            execution_enabled=execution_enabled,
            strategy_config_fingerprint=strategy_snapshot.production_fingerprint,
        )
        if not readiness.ready:
            print(json.dumps({"status": "blocked", "reasons": list(readiness.reasons)}))
            return 0

        def identity_guard() -> None:
            raw = client.account_info() or {}
            require_account_trade_mode(raw, expected=expected_mode)
            assert_account_matches(raw, require_binding=True)

        identity_guard()
        risk_result = latest_approved_risk_result(cfg.db_path, now_utc=datetime.now(UTC), symbol=cfg.symbols[0] if len(cfg.symbols) == 1 else None)
        if risk_result is None:
            print(json.dumps({"status": "idle", "reason": "NO_UNEXPIRED_APPROVED_RISK"}))
            return 0
        candidate = load_candidate(cfg.db_path, risk_result.candidate_id)
        if candidate is None:
            raise RuntimeError("APPROVED_RISK_CANDIDATE_MISSING")

        constants = client.execution_constants()
        broker = outcome.broker_state
        contracts = {item.symbol: item for item in broker.contracts}
        contract = contracts.get(risk_result.normalized_symbol)
        if contract is None:
            raise RuntimeError("RISK_SYMBOL_CONTRACT_MISSING")

        # Ollama must be configured before any new-entry attempt can pass.
        # Do not silently fall back to broker-only execution.
        try:
            ai_safety = D3DecisionSafetyGate.from_environment()
        except GateUnavailable:
            ai_safety = None
        def run_ai_gate(intent_to_check, broker_request):
            if ai_safety is None:
                raise GateUnavailable("AI_GATE_NOT_CONFIGURED")
            observed = {row.symbol: row for row in broker.ticks}
            quoted = observed.get(intent_to_check.symbol)
            if quoted is None:
                raise GateUnavailable("AI_GATE_BROKER_TICK_MISSING")
            point = D(str(contract.point))
            spread_points = ((D(str(quoted.ask)) - D(str(quoted.bid))) / point) if point > 0 else D("0")
            order_entry = D(str(intent_to_check.entry))
            stop = D(str(intent_to_check.stop_loss))
            target = D(str(intent_to_check.take_profit))
            return ai_safety(intent_to_check, broker_request, context={
                "strategy_id": candidate.strategy_id,
                "account_trade_mode": expected_mode.name,
                "tick_age_ms": max(0, int(datetime.now(UTC).timestamp() * 1000) - int(quoted.time_msc)),
                "bid": quoted.bid, "ask": quoted.ask,
                "spread_points": round(float(spread_points), 2),
                "stop_distance_points": round(float(abs(order_entry - stop) / point), 2) if point > 0 else 0,
                "target_distance_points": round(float(abs(target - order_entry) / point), 2) if point > 0 else 0,
            })

        service = GuardedExecutionService(
            db_path=cfg.db_path,
            execution_enabled=execution_enabled,
            identity_guard=identity_guard,
            safety_gate=run_ai_gate if ai_safety is not None else None,
            require_safety_gate=True,
        )
        intent = service.create_intent(risk_result, now_utc=datetime.now(UTC))
        prefer_limit = candidate.strategy_id in {"smc_pro_fvg_v2"}
        ticks = {item.symbol: item for item in broker.ticks}
        tick = ticks[intent.symbol]
        request = build_smart_trade_request(intent, contract=contract, tick=tick, constants=constants, policy=_policy(), prefer_limit=prefer_limit)
        if intent.state.value == "RISK_APPROVED":
            intent = service.preflight(
                intent.intent_id,
                now_utc=datetime.now(UTC),
                request=request,
                check=client.order_check,
                is_passed=order_check_passed,
            )
        if intent.state.value != "PREFLIGHT_PASSED":
            print(json.dumps({"status": "rejected", "intent_id": intent.intent_id, "reason": intent.last_reason}))
            return 6

        profile = load_risk_profile()

        def fresh_revalidate(current_intent):
            fresh_now = datetime.now(UTC)
            fresh = coordinator.sync_once(now_utc=fresh_now)
            if not fresh.ready or fresh.broker_state is None or fresh.safety is None or not fresh.raw_account:
                raise RuntimeError(f"FRESH_SYNC_FAILED:{fresh.reason}")
            require_account_trade_mode(fresh.raw_account, expected=expected_mode)
            assert_account_matches(fresh.raw_account, require_binding=True)
            fresh_broker = fresh.broker_state
            fresh_contracts = {item.symbol: item for item in fresh_broker.contracts}
            fresh_ticks = {item.symbol: item for item in fresh_broker.ticks}
            fresh_contract = fresh_contracts[current_intent.symbol]
            tick = fresh_ticks[current_intent.symbol]
            context = build_risk_context(cfg.db_path, fresh_broker, now_utc=fresh_now)
            tick_age = max(D("0"), D(str((fresh_now.timestamp() * 1000 - tick.time_msc) / 1000.0)))
            context = replace(context, tick_age_seconds=tick_age)
            order_types = {"BUY": constants["ORDER_TYPE_BUY"], "SELL": constants["ORDER_TYPE_SELL"]}

            def calc_profit(side: str, symbol: str, volume: D, open_price: D, close_price: D) -> D:
                value = client.order_calc_profit(order_types[side], symbol, float(volume), float(open_price), float(close_price))
                if value is None:
                    raise RuntimeError("order_calc_profit returned None")
                return D(str(value))

            def calc_margin(side: str, symbol: str, volume: D, open_price: D) -> D:
                value = client.order_calc_margin(order_types[side], symbol, float(volume), float(open_price))
                if value is None:
                    raise RuntimeError("order_calc_margin returned None")
                return D(str(value))

            refreshed = BrokerAwareRiskEngine(profile).evaluate(
                candidate_input(candidate, now_utc=fresh_now, contract=fresh_contract),
                account=fresh_broker.account,
                contract=fresh_contract,
                tick=tick,
                safety=fresh.safety,
                context=context,
                calc_profit=calc_profit,
                calc_margin=calc_margin,
                now_utc=fresh_now,
            )
            fixed_lot_raw = load_fixed_lot()
            return apply_fixed_volume(refreshed, fixed_volume=D(fixed_lot_raw) if fixed_lot_raw is not None else None, calc_profit=calc_profit, calc_margin=calc_margin)

        classifier = MT5RetcodeClassifier(constants)
        intent = service.send_once(
            intent.intent_id,
            now_utc=datetime.now(UTC),
            request=request,
            send=client.order_send,
            classify=classifier.classify,
            fresh_revalidate=fresh_revalidate,
            final_check=client.order_check,
            is_final_check_passed=order_check_passed,
        )

        post = coordinator.sync_once(now_utc=datetime.now(UTC))
        if post.broker_state is not None:
            reconciled = service.reconcile(
                intent.intent_id,
                orders=post.broker_state.recent_orders,
                deals=post.broker_state.recent_deals,
                positions=post.broker_state.positions,
            )
            intent = reconciled.intent

        log_audit_event(
            cfg.db_path,
            event_type="EXECUTION_RUNNER_RESULT",
            source="execution_runner",
            symbol=intent.symbol,
            entity_id=intent.intent_id,
            payload={"account_trade_mode": expected_mode.name, "state": intent.state.value, "reason": intent.last_reason},
        )
        print(json.dumps({"status": "ok", "intent_id": intent.intent_id, "state": intent.state.value, "reason": intent.last_reason}))
        return 0
    except Exception as exc:
        log_audit_event(
            cfg.db_path,
            event_type="EXECUTION_RUNNER_BLOCKED",
            source="execution_runner",
            payload={"error": f"{type(exc).__name__}: {exc}"},
        )
        print(json.dumps({"status": "blocked", "reasons": [f"{type(exc).__name__}: {exc}"]}))
        return 7
    finally:
        coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
