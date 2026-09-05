#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone

from forex_ai.config import load_execution_enabled, load_runtime_config
from forex_ai.execution.close_service import GuardedCloseService
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.external_positions import trace_external_positions
from forex_ai.journal.integration_repository import SQLiteIntentRepository
from forex_ai.journal.repository import insert_position_snapshots
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import assert_account_matches
from forex_ai.runtime.resilience import MT5ResyncCoordinator

UTC = timezone.utc


def _trace_position_truth(cfg) -> tuple[dict, ...]:
    """Capture broker positions independently of market-data readiness."""
    client = MT5Client(cfg)
    if not client.connect():
        raise RuntimeError("POSITION_TRACE_MT5_CONNECT_FAILED")
    try:
        positions = tuple(client.positions())
        now = datetime.now(UTC)
        insert_position_snapshots(cfg.db_path, list(positions))
        trace_external_positions(cfg.db_path, list(positions), observed_at_utc=now)
        return positions
    finally:
        client.close()


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)

    try:
        raw_positions = _trace_position_truth(cfg)
    except Exception as exc:
        log_audit_event(
            cfg.db_path,
            event_type="POSITION_TRACE_ERROR",
            source="execution_reconciler",
            payload={"error": f"{type(exc).__name__}: {exc}"},
        )
        print(json.dumps({"status": "blocked", "reason": f"POSITION_TRACE_ERROR:{type(exc).__name__}"}))
        return 2

    repo = SQLiteIntentRepository(cfg.db_path)
    intents = tuple(
        intent
        for intent in repo.all()
        if intent.state.value not in {"REJECTED", "CANCELLED", "CLOSED"}
    )
    if not intents:
        print(json.dumps({
            "status": "ok",
            "reconciled": 0,
            "external_position_count": len(raw_positions),
            "reason": "NO_ACTIVE_INTENTS",
        }))
        return 0

    client = MT5Client(cfg)
    coordinator = MT5ResyncCoordinator(
        client=client,
        symbols=cfg.symbols,
        db_path=cfg.db_path,
        bars_count=52,
        load_history=True,
        history_refresh_seconds=0,
    )
    try:
        outcome = coordinator.sync_once(now_utc=datetime.now(UTC))
        if outcome.broker_state is None:
            print(json.dumps({"status": "blocked", "reason": outcome.reason or "BROKER_STATE_UNAVAILABLE"}))
            return 3

        broker = outcome.broker_state
        constants = client.execution_constants()

        def identity_guard() -> None:
            raw = client.account_info() or {}
            assert_account_matches(raw, require_binding=True)

        entry_service = GuardedExecutionService(
            db_path=cfg.db_path,
            execution_enabled=load_execution_enabled(),
            identity_guard=identity_guard,
        )
        close_service = GuardedCloseService(
            db_path=cfg.db_path,
            execution_enabled=load_execution_enabled(),
            identity_guard=identity_guard,
        )

        results: list[dict] = []
        for intent in intents:
            reconciled = entry_service.reconcile(
                intent.intent_id,
                orders=broker.recent_orders,
                deals=broker.recent_deals,
                positions=broker.positions,
            ).intent
            reconciled = close_service.reconcile_broker_exit(
                reconciled.intent_id,
                now_utc=datetime.now(UTC),
                positions=broker.positions,
                deals=broker.recent_deals,
                deal_reason_sl=constants.get("DEAL_REASON_SL"),
                deal_reason_tp=constants.get("DEAL_REASON_TP"),
            )
            results.append({
                "intent_id": reconciled.intent_id,
                "state": reconciled.state.value,
                "reason": reconciled.last_reason,
            })

        log_audit_event(
            cfg.db_path,
            event_type="EXECUTION_RECONCILIATION",
            source="execution_reconciler",
            payload={"count": len(results), "states": [item["state"] for item in results]},
        )
        print(json.dumps({"status": "ok", "reconciled": len(results), "results": results}))
        return 0
    finally:
        coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
