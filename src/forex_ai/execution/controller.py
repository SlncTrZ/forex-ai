from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from forex_ai.execution.state import ExecutionState, IntentRepository, OrderIntent


@dataclass(frozen=True)
class SendOutcome:
    accepted: bool
    broker_order_ticket: int | None = None
    broker_position_ticket: int | None = None
    reason: str | None = None
    unknown: bool = False
    partial: bool = False
    filled_volume: Decimal = Decimal("0")


class ExecutionController:
    """Persistence-first lifecycle controller.

    It never retries SEND_STARTED or UNKNOWN intents. Callers must reconcile those
    states against broker evidence before another execution attempt is possible.
    """

    def __init__(self, repository: IntentRepository):
        self.repository = repository

    def register(self, intent: OrderIntent) -> OrderIntent:
        existing = self.repository.get_by_idempotency_key(intent.idempotency_key)
        if existing is not None:
            return existing
        if intent.state is not ExecutionState.INTENT_CREATED:
            raise ValueError("new intent must start at INTENT_CREATED")
        self.repository.save(intent)
        return intent

    def approve_risk(self, intent_id: str) -> OrderIntent:
        return self._transition(intent_id, ExecutionState.RISK_APPROVED, "RISK_APPROVED")

    def preflight(
        self,
        intent_id: str,
        request: dict[str, Any],
        check: Callable[[dict[str, Any]], dict[str, Any] | None],
        is_passed: Callable[[dict[str, Any] | None], bool],
    ) -> OrderIntent:
        intent = self._require(intent_id)
        if intent.state is not ExecutionState.RISK_APPROVED:
            raise ValueError("preflight requires RISK_APPROVED")
        result = check(request)
        target = ExecutionState.PREFLIGHT_PASSED if is_passed(result) else ExecutionState.REJECTED
        return self._save(intent.transition(target, reason="PREFLIGHT_PASSED" if target is ExecutionState.PREFLIGHT_PASSED else "PREFLIGHT_REJECTED"))

    def begin_send(self, intent_id: str) -> OrderIntent:
        intent = self._require(intent_id)
        if intent.state is not ExecutionState.PREFLIGHT_PASSED:
            raise ValueError("send requires PREFLIGHT_PASSED; reconcile uncertain states before retry")
        return self._save(intent.transition(ExecutionState.SEND_STARTED, reason="SEND_STARTED"))

    def send_once(
        self,
        intent_id: str,
        request: dict[str, Any],
        send: Callable[[dict[str, Any]], dict[str, Any] | None],
        classify: Callable[[dict[str, Any] | None], SendOutcome],
    ) -> OrderIntent:
        intent = self.begin_send(intent_id)
        try:
            response = send(request)
        except Exception:
            # Once SEND_STARTED is persisted, any broker-call exception leaves the
            # external outcome uncertain. Reconciliation is required before retry.
            return self._save(intent.transition(ExecutionState.UNKNOWN, reason="SEND_RESULT_UNKNOWN"))
        try:
            outcome = classify(response)
        except Exception:
            return self._save(intent.transition(ExecutionState.UNKNOWN, reason="SEND_CLASSIFICATION_UNKNOWN"))
        if outcome.unknown:
            return self._save(intent.transition(ExecutionState.UNKNOWN, reason=outcome.reason or "SEND_RESULT_UNKNOWN"))
        if not outcome.accepted:
            return self._save(intent.transition(ExecutionState.REJECTED, reason=outcome.reason or "BROKER_REJECTED"))
        target = ExecutionState.PARTIALLY_FILLED if outcome.partial else ExecutionState.ACCEPTED
        return self._save(
            intent.transition(
                target,
                reason=outcome.reason or ("BROKER_PARTIAL" if outcome.partial else "BROKER_ACCEPTED"),
                broker_order_ticket=outcome.broker_order_ticket,
                broker_position_ticket=outcome.broker_position_ticket,
                filled_volume=outcome.filled_volume if outcome.partial else intent.filled_volume,
            )
        )

    def _transition(self, intent_id: str, target: ExecutionState, reason: str) -> OrderIntent:
        return self._save(self._require(intent_id).transition(target, reason=reason))

    def _require(self, intent_id: str) -> OrderIntent:
        intent = self.repository.get(intent_id)
        if intent is None:
            raise KeyError(intent_id)
        return intent

    def _save(self, intent: OrderIntent) -> OrderIntent:
        self.repository.save(intent)
        return intent
