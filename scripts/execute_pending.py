#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from forex_ai.config import load_execution_enabled, load_fixed_lot, load_risk_profile, load_runtime_config
from forex_ai.execution.account_mode import expected_trade_mode_for_runtime, require_account_trade_mode
from forex_ai.execution.live_canary import assess_live_canary_readiness
from forex_ai.execution.demo_campaign import assess_demo_campaign_readiness
from forex_ai.execution.mt5 import MT5MarketRequestPolicy, MT5RetcodeClassifier, build_market_request, order_check_passed
from forex_ai.integration.adapters import candidate_input
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.integration_repository import latest_approved_risk_result, load_candidate
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches, assert_account_matches
from forex_ai.risk.broker_engine import apply_fixed_volume, BrokerAwareRiskEngine
from forex_ai.runtime.resilience import MT5ResyncCoordinator
from forex_ai.execution.auto_week import auto_live_window
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


def _readiness(cfg, *, account_trade_mode: int | None, account_identity_bound: bool, execution_enabled: bool,
               strategy_config_fingerprint: str):
    now = datetime.now(UTC)
    if cfg.mode == "DEMO":
        return assess_demo_campaign_readiness(
            db_path=cfg.db_path,
            mode=cfg.mode,
            execution_enabled=execution_enabled,
            campaign_id=os.getenv("FOREX_AI_DEMO_CAMPAIGN_ID", ""),
            account_trade_mode=account_trade_mode,
            account_identity_bound=account_identity_bound,
            now_utc=now,
        )
    if cfg.mode == "LIVE_CANARY":
        approval = os.getenv("FOREX_AI_STRATEGY_APPROVAL_FILE")
        return assess_live_canary_readiness(
            db_path=cfg.db_path,
            mode=cfg.mode,
            execution_enabled=execution_enabled,
            symbols=cfg.symbols,
            risk_profile=load_risk_profile(),
            strategy_config_fingerprint=strategy_config_fingerprint,
            approval_path=Path(approval).expanduser() if approval else None,
            account_trade_mode=account_trade_mode,
            account_identity_bound=account_identity_bound,
            now_utc=now,
        )
    raise RuntimeError(f"EXECUTION_MODE_NOT_SUPPORTED:{cfg.mode}")


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    now = datetime.now(UTC)
    if cfg.mode == "LIVE_CANARY" and not auto_live_window(now):
        print(json.dumps({"status": "idle", "reason": "OUTSIDE_AUTO_LIVE_WINDOW"}))
        return 0
    execution_enabled = load_execution_enabled()
    strategy_snapshot = load_strategy_snapshot()
    if cfg.mode not in {"DEMO", "LIVE_CANARY"}:
        print(json.dumps({"status": "blocked", "reasons": ["MODE_NOT_EXECUTION_RUNNER"]}))
        return 3

    client = MT5Client(cfg)
    coordinator = MT5ResyncCoordinator(
        client=client,
        symbols=cfg.symbols,
        db_path=cfg.db_path,
        bars_count=required_raw_bars(strategy_snapshot),
        load_history=True,
        history_refresh_seconds=0,
    )
    try:
        now = datetime.now(UTC)
        outcome = coordinator.sync_once(now_utc=now)
        if not outcome.ready or outcome.broker_state is None or outcome.safety is None or not outcome.raw_account:
            print(json.dumps({"status": "blocked", "reasons": [outcome.reason or "BROKER_STATE_NOT_READY"]}))
            return 4

        expected_mode = expected_trade_mode_for_runtime(cfg.mode)
        require_account_trade_mode(outcome.raw_account, expected=expected_mode)
        readiness = _readiness(
            cfg,
            account_trade_mode=int(outcome.raw_account.get("trade_mode")),
            account_identity_bound=account_matches(outcome.raw_account),
            execution_enabled=execution_enabled,
            strategy_config_fingerprint=strategy_snapshot.production_fingerprint,
        )
        if not readiness.ready:
            print(json.dumps({"status": "blocked", "reasons": list(readiness.reasons)}))
            return 5

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

        service = GuardedExecutionService(
            db_path=cfg.db_path,
            execution_enabled=execution_enabled,
            identity_guard=identity_guard,
        )
        intent = service.create_intent(risk_result, now_utc=datetime.now(UTC))
        request = build_market_request(intent, contract=contract, constants=constants, policy=_policy())
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
                candidate_input(candidate, now_utc=fresh_now),
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
            payload={"mode": cfg.mode, "state": intent.state.value, "reason": intent.last_reason},
        )
        print(json.dumps({"status": "ok", "intent_id": intent.intent_id, "state": intent.state.value, "reason": intent.last_reason}))
        return 0
    except Exception as exc:
        log_audit_event(
            cfg.db_path,
            event_type="EXECUTION_RUNNER_BLOCKED",
            source="execution_runner",
            payload={"mode": cfg.mode, "error": f"{type(exc).__name__}: {exc}"},
        )
        print(json.dumps({"status": "blocked", "reasons": [f"{type(exc).__name__}: {exc}"]}))
        return 7
    finally:
        coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
