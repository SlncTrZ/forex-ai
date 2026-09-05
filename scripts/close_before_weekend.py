#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from forex_ai.config import load_execution_enabled, load_runtime_config
from forex_ai.execution.account_mode import expected_trade_mode_for_runtime, require_account_trade_mode
from forex_ai.execution.close_service import GuardedCloseService
from forex_ai.execution.mt5 import MT5MarketRequestPolicy, MT5RetcodeClassifier, build_close_request, order_check_passed
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.integration_repository import SQLiteIntentRepository
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import assert_account_matches
from forex_ai.runtime.market_schedule import weekend_force_close_due
from forex_ai.runtime.resilience import MT5ResyncCoordinator
from forex_ai.strategy.config import load_strategy_snapshot, required_raw_bars

UTC = timezone.utc
OPEN_STATES = {"PROTECTION_VERIFIED", "RECONCILED"}


def _policy() -> MT5MarketRequestPolicy:
    return MT5MarketRequestPolicy(
        deviation_points=int(os.getenv("FOREX_AI_DEVIATION_POINTS", "20")),
        magic=int(os.getenv("FOREX_AI_MAGIC", "260904")),
        comment_prefix="FXAI",
    )


def main() -> int:
    now = datetime.now(UTC)
    if not weekend_force_close_due(now):
        print(json.dumps({"status": "idle", "reason": "WEEKEND_CLOSE_NOT_DUE"}))
        return 0

    cfg = load_runtime_config()
    initialize(cfg.db_path)
    execution_enabled = load_execution_enabled()
    if not execution_enabled:
        log_audit_event(
            cfg.db_path,
            event_type="WEEKEND_CLOSE_BLOCKED",
            source="weekend_close",
            payload={"reason": "EXECUTION_DISABLED"},
        )
        print(json.dumps({"status": "blocked", "reason": "EXECUTION_DISABLED"}))
        return 2

    if cfg.mode not in {"DEMO", "LIVE_CANARY"}:
        print(json.dumps({"status": "blocked", "reason": "MODE_NOT_EXECUTION_CAPABLE"}))
        return 3

    strategy_snapshot = load_strategy_snapshot()
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
        outcome = coordinator.sync_once(now_utc=now)
        if not outcome.ready or outcome.broker_state is None or not outcome.raw_account:
            reason = outcome.reason or "BROKER_STATE_NOT_READY"
            log_audit_event(
                cfg.db_path,
                event_type="WEEKEND_CLOSE_BLOCKED",
                source="weekend_close",
                payload={"reason": reason},
            )
            print(json.dumps({"status": "blocked", "reason": reason}))
            return 4

        expected_mode = expected_trade_mode_for_runtime(cfg.mode)

        def identity_guard() -> None:
            raw = client.account_info() or {}
            require_account_trade_mode(raw, expected=expected_mode)
            assert_account_matches(raw, require_binding=True)

        require_account_trade_mode(outcome.raw_account, expected=expected_mode)
        identity_guard()

        broker = outcome.broker_state
        positions_by_ticket = {position.ticket: position for position in broker.positions}
        contracts = {contract.symbol: contract for contract in broker.contracts}
        ticks = {tick.symbol: tick for tick in broker.ticks}
        repo = SQLiteIntentRepository(cfg.db_path)
        intents = tuple(
            intent
            for intent in repo.all()
            if intent.state.value in OPEN_STATES and intent.broker_position_ticket is not None
        )
        managed_tickets = {intent.broker_position_ticket for intent in intents}
        external_tickets = sorted(ticket for ticket in positions_by_ticket if ticket not in managed_tickets)

        if not intents:
            log_audit_event(
                cfg.db_path,
                event_type="WEEKEND_CLOSE_IDLE",
                source="weekend_close",
                payload={"external_position_tickets": external_tickets},
            )
            print(json.dumps({
                "status": "idle",
                "reason": "NO_MANAGED_OPEN_POSITIONS",
                "external_position_tickets": external_tickets,
            }))
            return 0

        constants = client.execution_constants()
        classifier = MT5RetcodeClassifier(constants)
        service = GuardedCloseService(
            db_path=cfg.db_path,
            execution_enabled=execution_enabled,
            identity_guard=identity_guard,
        )
        submitted: list[str] = []
        skipped: list[dict[str, object]] = []

        for intent in intents:
            ticket = intent.broker_position_ticket
            position = positions_by_ticket.get(ticket)
            if position is None:
                skipped.append({"intent_id": intent.intent_id, "reason": "BROKER_POSITION_NOT_PRESENT"})
                continue
            contract = contracts.get(position.symbol)
            tick = ticks.get(position.symbol)
            if contract is None or tick is None:
                skipped.append({"intent_id": intent.intent_id, "reason": "CONTRACT_OR_TICK_MISSING"})
                continue
            request = build_close_request(
                position,
                tick=tick,
                contract=contract,
                constants=constants,
                policy=_policy(),
            )
            service.submit_close_once(
                intent.intent_id,
                now_utc=datetime.now(UTC),
                exit_reason="WEEKEND_CLOSE",
                request=request,
                final_check=client.order_check,
                is_final_check_passed=order_check_passed,
                send=client.close_position,
                classify=classifier.classify,
            )
            submitted.append(intent.intent_id)

        if submitted:
            post = coordinator.sync_once(now_utc=datetime.now(UTC))
            if post.broker_state is not None:
                for intent_id in submitted:
                    service.reconcile_close(
                        intent_id,
                        now_utc=datetime.now(UTC),
                        positions=post.broker_state.positions,
                        deals=post.broker_state.recent_deals,
                    )

        log_audit_event(
            cfg.db_path,
            event_type="WEEKEND_CLOSE_RESULT",
            source="weekend_close",
            payload={
                "submitted_intent_ids": submitted,
                "skipped": skipped,
                "external_position_tickets_untouched": external_tickets,
            },
        )
        print(json.dumps({
            "status": "ok",
            "submitted": submitted,
            "skipped": skipped,
            "external_position_tickets_untouched": external_tickets,
        }))
        return 0
    except Exception as exc:
        log_audit_event(
            cfg.db_path,
            event_type="WEEKEND_CLOSE_ERROR",
            source="weekend_close",
            payload={"error": f"{type(exc).__name__}: {exc}"},
        )
        print(json.dumps({"status": "blocked", "reason": f"{type(exc).__name__}: {exc}"}))
        return 5
    finally:
        coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
