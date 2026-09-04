from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from forex_ai.execution.controller import ExecutionController, SendOutcome
from forex_ai.execution.reconcile import ReconcileResult, reconcile_intent
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.journal.integration_repository import (
    SQLiteIntentRepository,
    load_trading_control,
    persist_execution_broker_event,
)
from forex_ai.mt5.contracts import BrokerDeal, BrokerOrder, BrokerPosition
from forex_ai.risk.broker_engine import BrokerRiskResult


class ExecutionDisarmed(RuntimeError):
    pass


class GuardedExecutionService:
    """Persistent execution boundary with explicit arming checks.

    The service checks control state before preflight and again immediately
    before send. It also blocks on unresolved SEND_STARTED/UNKNOWN intents.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        execution_enabled: bool,
        identity_guard: Callable[[], None] | None = None,
    ):
        self.db_path = db_path
        self.execution_enabled = execution_enabled
        self.identity_guard = identity_guard
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
            symbol=result.normalized_symbol, side=result.side,
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
        now = now_utc.astimezone(timezone.utc)
        self._assert_entry_allowed(now, ignore_intent_id=intent_id)
        def audited_check(payload: dict[str, Any]) -> dict[str, Any] | None:
            try:
                response = check(payload)
                return response
            except Exception:
                persist_execution_broker_event(
                    self.db_path,
                    intent_id=intent_id,
                    timestamp_utc=now,
                    phase="PREFLIGHT",
                    request=payload,
                    response=None,
                    outcome_class="EXCEPTION",
                )
                raise

        def audited_passed(response: dict[str, Any] | None) -> bool:
            passed = is_passed(response)
            persist_execution_broker_event(
                self.db_path,
                intent_id=intent_id,
                timestamp_utc=now,
                phase="PREFLIGHT",
                request=request,
                response=response,
                outcome_class="PASSED" if passed else "REJECTED",
            )
            return passed

        return self.controller.preflight(intent_id, request, audited_check, audited_passed)

    def send_once(
        self,
        intent_id: str,
        *,
        now_utc: datetime,
        request: dict[str, Any],
        send: Callable[[dict[str, Any]], dict[str, Any] | None],
        classify: Callable[[dict[str, Any] | None], SendOutcome],
        fresh_revalidate: Callable[[OrderIntent], BrokerRiskResult],
        final_check: Callable[[dict[str, Any]], dict[str, Any] | None],
        is_final_check_passed: Callable[[dict[str, Any] | None], bool],
    ) -> OrderIntent:
        now = now_utc.astimezone(timezone.utc)
        self._assert_entry_allowed(now, ignore_intent_id=intent_id)
        current = self.repository.get(intent_id)
        if current is None:
            raise KeyError(intent_id)
        # Risk approval is a short-lived lease. Refresh broker/account/safety/risk
        # immediately before the final broker-side order_check and send.
        try:
            fresh_result = fresh_revalidate(current)
        except Exception as exc:
            return self._reject_before_send(current, f"FRESH_REVALIDATION_EXCEPTION:{type(exc).__name__}")
        mismatch = self._fresh_risk_mismatch(current, fresh_result, now_utc=now)
        if mismatch is not None:
            return self._reject_before_send(current, mismatch)

        try:
            final_response = final_check(request)
            final_passed = is_final_check_passed(final_response)
        except Exception as exc:
            persist_execution_broker_event(
                self.db_path,
                intent_id=intent_id,
                timestamp_utc=now,
                phase="FINAL_PREFLIGHT",
                request=request,
                response=None,
                outcome_class="EXCEPTION",
            )
            return self._reject_before_send(current, f"FINAL_PREFLIGHT_EXCEPTION:{type(exc).__name__}")
        persist_execution_broker_event(
            self.db_path,
            intent_id=intent_id,
            timestamp_utc=now,
            phase="FINAL_PREFLIGHT",
            request=request,
            response=final_response,
            outcome_class="PASSED" if final_passed else "REJECTED",
        )
        if not final_passed:
            return self._reject_before_send(current, "FINAL_PREFLIGHT_REJECTED")
        self._assert_entry_allowed(now, ignore_intent_id=intent_id)

        def audited_send(payload: dict[str, Any]) -> dict[str, Any] | None:
            try:
                return send(payload)
            except Exception:
                persist_execution_broker_event(
                    self.db_path,
                    intent_id=intent_id,
                    timestamp_utc=now,
                    phase="SEND",
                    request=payload,
                    response=None,
                    outcome_class="UNKNOWN_EXCEPTION",
                )
                raise

        def audited_classify(response: dict[str, Any] | None) -> SendOutcome:
            try:
                outcome = classify(response)
            except Exception:
                persist_execution_broker_event(
                    self.db_path,
                    intent_id=intent_id,
                    timestamp_utc=now,
                    phase="SEND",
                    request=request,
                    response=response,
                    outcome_class="CLASSIFIER_EXCEPTION",
                )
                raise
            if outcome.unknown:
                outcome_class = "UNKNOWN"
            elif outcome.partial:
                outcome_class = "PARTIAL"
            elif outcome.accepted:
                outcome_class = "ACCEPTED"
            else:
                outcome_class = "REJECTED"
            persist_execution_broker_event(
                self.db_path,
                intent_id=intent_id,
                timestamp_utc=now,
                phase="SEND",
                request=request,
                response=response,
                outcome_class=outcome_class,
            )
            return outcome

        return self.controller.send_once(intent_id, request, audited_send, audited_classify)

    @staticmethod
    def _fresh_risk_mismatch(intent: OrderIntent, result: BrokerRiskResult, *, now_utc: datetime) -> str | None:
        if not result.approved:
            return "FRESH_RISK_REJECTED"
        if result.expires_at_utc.astimezone(timezone.utc) <= now_utc:
            return "FRESH_RISK_EXPIRED"
        checks = (
            (result.candidate_id == intent.candidate_id, "FRESH_CANDIDATE_CHANGED"),
            (result.side == intent.side, "FRESH_SIDE_CHANGED"),
            (result.normalized_symbol == intent.symbol, "FRESH_SYMBOL_CHANGED"),
            (result.normalized_volume == intent.volume, "FRESH_VOLUME_CHANGED"),
            (result.executable_entry == intent.entry, "FRESH_ENTRY_CHANGED"),
            (result.stop_loss == intent.stop_loss, "FRESH_STOP_CHANGED"),
            (result.take_profit == intent.take_profit, "FRESH_TARGET_CHANGED"),
        )
        for valid, reason in checks:
            if not valid:
                return reason
        return None

    def _reject_before_send(self, intent: OrderIntent, reason: str) -> OrderIntent:
        if intent.state not in {ExecutionState.RISK_APPROVED, ExecutionState.PREFLIGHT_PASSED}:
            raise ExecutionDisarmed(reason)
        rejected = intent.transition(ExecutionState.REJECTED, reason=reason)
        self.repository.save(rejected)
        return rejected

    def reconcile(
        self,
        intent_id: str,
        *,
        orders: tuple[BrokerOrder, ...] = (),
        deals: tuple[BrokerDeal, ...] = (),
        positions: tuple[BrokerPosition, ...] = (),
    ) -> ReconcileResult:
        current = self.repository.get(intent_id)
        if current is None:
            raise KeyError(intent_id)
        result = reconcile_intent(current, orders=orders, deals=deals, positions=positions)
        for transition in result.transition_path:
            self.repository.save(transition)
        return result

    def _assert_entry_allowed(self, now_utc: datetime, *, ignore_intent_id: str | None = None) -> None:
        if not self.execution_enabled:
            raise ExecutionDisarmed("execution_enabled=false")
        if self.identity_guard is None:
            raise ExecutionDisarmed("account identity guard is required")
        try:
            self.identity_guard()
        except Exception as exc:
            raise ExecutionDisarmed(f"account identity check failed: {exc}") from exc
        control = load_trading_control(self.db_path)
        if not control.allows_new_entries(now_utc=now_utc):
            raise ExecutionDisarmed("trading control is disarmed, expired, in maintenance, or kill-switched")
        unresolved = tuple(
            intent for intent in self.repository.all()
            if intent.intent_id != ignore_intent_id and intent.state in {ExecutionState.SEND_STARTED, ExecutionState.UNKNOWN}
        )
        if unresolved:
            raise ExecutionDisarmed("unresolved execution intent requires reconciliation")
