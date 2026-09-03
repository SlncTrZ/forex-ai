from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from forex_ai.execution.controller import ExecutionController, SendOutcome
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.journal.integration_repository import SQLiteIntentRepository, load_trading_control
from forex_ai.risk.broker_engine import BrokerRiskResult


class ExecutionDisarmed(RuntimeError):
    pass


class GuardedExecutionService:
    """Persistent execution boundary with explicit arming checks.

    The service checks control state before preflight and again immediately
    before send. It also blocks on unresolved SEND_STARTED/UNKNOWN intents.
    """

    def __init__(self, *, db_path: Path, execution_enabled: bool):
        self.db_path = db_path
        self.execution_enabled = execution_enabled
        self.repository = SQLiteIntentRepository(db_path)
        self.controller = ExecutionController(self.repository)

    def create_intent(self, result: BrokerRiskResult, *, now_utc: datetime) -> OrderIntent:
        now = now_utc.astimezone(timezone.utc)
        if not result.approved:
            raise ExecutionDisarmed("risk result is not approved")
        if result.expires_at_utc.astimezone(timezone.utc) <= now:
            raise ExecutionDisarmed("risk result expired")
        self._assert_entry_allowed(now)
        key = OrderIntent.derive_idempotency_key(
            result.candidate_id, result.risk_profile_fingerprint, result.safety_snapshot_fingerprint
        )
        intent = OrderIntent(
            intent_id=f"intent-{key[:32]}", candidate_id=result.candidate_id, idempotency_key=key,
            symbol=result.normalized_symbol, side="BUY" if result.stop_loss < result.executable_entry else "SELL",
            volume=result.normalized_volume, entry=result.executable_entry, stop_loss=result.stop_loss,
            take_profit=result.take_profit, state=ExecutionState.INTENT_CREATED, created_at_utc=now,
        )
        registered = self.controller.register(intent)
        if registered.state is ExecutionState.INTENT_CREATED:
            registered = self.controller.approve_risk(registered.intent_id)
        return registered

    def preflight(
        self,
        intent_id: str,
        *,
        now_utc: datetime,
        request: dict[str, Any],
        check: Callable[[dict[str, Any]], dict[str, Any] | None],
        is_passed: Callable[[dict[str, Any] | None], bool],
    ) -> OrderIntent:
        self._assert_entry_allowed(now_utc.astimezone(timezone.utc), ignore_intent_id=intent_id)
        return self.controller.preflight(intent_id, request, check, is_passed)

    def send_once(
        self,
        intent_id: str,
        *,
        now_utc: datetime,
        request: dict[str, Any],
        send: Callable[[dict[str, Any]], dict[str, Any] | None],
        classify: Callable[[dict[str, Any] | None], SendOutcome],
    ) -> OrderIntent:
        self._assert_entry_allowed(now_utc.astimezone(timezone.utc), ignore_intent_id=intent_id)
        return self.controller.send_once(intent_id, request, send, classify)

    def _assert_entry_allowed(self, now_utc: datetime, *, ignore_intent_id: str | None = None) -> None:
        if not self.execution_enabled:
            raise ExecutionDisarmed("execution_enabled=false")
        control = load_trading_control(self.db_path)
        if not control.allows_new_entries(now_utc=now_utc):
            raise ExecutionDisarmed("trading control is disarmed, expired, in maintenance, or kill-switched")
        unresolved = tuple(
            intent for intent in self.repository.all()
            if intent.intent_id != ignore_intent_id and intent.state in {ExecutionState.SEND_STARTED, ExecutionState.UNKNOWN}
        )
        if unresolved:
            raise ExecutionDisarmed("unresolved execution intent requires reconciliation")
