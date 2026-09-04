from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from forex_ai.execution.controller import SendOutcome
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.journal.integration_repository import (
    SQLiteIntentRepository,
    load_trade_closure,
    persist_execution_broker_event,
    persist_trade_closure,
)
from forex_ai.mt5.contracts import BrokerDeal, BrokerPosition


class CloseDisarmed(RuntimeError):
    pass


class GuardedCloseService:
    """Explicit, auditable close path.

    Closing an existing position is intentionally not blocked by the entry kill
    switch. It still requires execution enablement and a matching account
    identity, then uses broker preflight, a single send, and broker-truth
    reconciliation before marking the lifecycle CLOSED.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        execution_enabled: bool,
        identity_guard: Callable[[], None] | None,
    ):
        self.db_path = db_path
        self.execution_enabled = execution_enabled
        self.identity_guard = identity_guard
        self.repository = SQLiteIntentRepository(db_path)

    def submit_close_once(
        self,
        intent_id: str,
        *,
        now_utc: datetime,
        exit_reason: str,
        request: dict[str, Any],
        final_check: Callable[[dict[str, Any]], dict[str, Any] | None],
        is_final_check_passed: Callable[[dict[str, Any] | None], bool],
        send: Callable[[dict[str, Any]], dict[str, Any] | None],
        classify: Callable[[dict[str, Any] | None], SendOutcome],
    ) -> OrderIntent:
        now = now_utc.astimezone(timezone.utc)
        self._assert_exit_allowed()
        intent = self._require_open_intent(intent_id)
        if not exit_reason.strip():
            raise ValueError("exit_reason is required")

        previous = load_trade_closure(self.db_path, intent_id)
        if previous is not None and previous["outcome_class"] in {"ACCEPTED", "PARTIAL", "UNKNOWN"}:
            raise CloseDisarmed("close already submitted; reconcile broker state before retry")

        try:
            check_response = final_check(request)
            passed = is_final_check_passed(check_response)
        except Exception:
            persist_trade_closure(
                self.db_path,
                intent_id=intent_id,
                requested_at_utc=now,
                exit_reason=exit_reason,
                request=request,
                response=None,
                outcome_class="PREFLIGHT_EXCEPTION",
            )
            raise

        persist_execution_broker_event(
            self.db_path,
            intent_id=intent_id,
            timestamp_utc=now,
            phase="CLOSE_PREFLIGHT",
            request=request,
            response=check_response,
            outcome_class="PASSED" if passed else "REJECTED",
        )
        if not passed:
            persist_trade_closure(
                self.db_path,
                intent_id=intent_id,
                requested_at_utc=now,
                exit_reason=exit_reason,
                request=request,
                response=check_response,
                outcome_class="PREFLIGHT_REJECTED",
            )
            return intent

        try:
            response = send(request)
            outcome = classify(response)
        except Exception:
            persist_execution_broker_event(
                self.db_path,
                intent_id=intent_id,
                timestamp_utc=now,
                phase="CLOSE_SEND",
                request=request,
                response=None,
                outcome_class="UNKNOWN_EXCEPTION",
            )
            persist_trade_closure(
                self.db_path,
                intent_id=intent_id,
                requested_at_utc=now,
                exit_reason=exit_reason,
                request=request,
                response=None,
                outcome_class="UNKNOWN",
            )
            return intent

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
            phase="CLOSE_SEND",
            request=request,
            response=response,
            outcome_class=outcome_class,
        )
        persist_trade_closure(
            self.db_path,
            intent_id=intent_id,
            requested_at_utc=now,
            exit_reason=exit_reason,
            request=request,
            response=response,
            outcome_class=outcome_class,
        )
        return intent

    def reconcile_close(
        self,
        intent_id: str,
        *,
        now_utc: datetime,
        positions: tuple[BrokerPosition, ...],
        deals: tuple[BrokerDeal, ...] = (),
    ) -> OrderIntent:
        current = self.repository.get(intent_id)
        if current is None:
            raise KeyError(intent_id)
        closure = load_trade_closure(self.db_path, intent_id)
        if closure is None:
            return current
        if current.broker_position_ticket is None:
            raise CloseDisarmed("intent has no broker position ticket")
        if any(position.ticket == current.broker_position_ticket for position in positions):
            return current
        if current.state not in {ExecutionState.PROTECTION_VERIFIED, ExecutionState.RECONCILED}:
            return current

        related = tuple(deal for deal in deals if deal.position_id == current.broker_position_ticket)
        final_pnl = sum((Decimal(str(deal.profit)) for deal in related), Decimal("0")) if related else None
        closed = current.transition(ExecutionState.CLOSED, reason=str(closure["exit_reason"]))
        self.repository.save(closed)
        persist_trade_closure(
            self.db_path,
            intent_id=intent_id,
            requested_at_utc=datetime.fromisoformat(closure["requested_at_utc"]),
            exit_reason=str(closure["exit_reason"]),
            request={"original_request_sha256": closure["request_sha256"]},
            response=None,
            outcome_class="CLOSED_RECONCILED",
            closed_at_utc=now_utc,
            final_pnl=final_pnl,
        )
        return closed

    def reconcile_broker_exit(
        self,
        intent_id: str,
        *,
        now_utc: datetime,
        positions: tuple[BrokerPosition, ...],
        deals: tuple[BrokerDeal, ...],
        deal_reason_sl: int | None = None,
        deal_reason_tp: int | None = None,
    ) -> OrderIntent:
        current = self.repository.get(intent_id)
        if current is None:
            raise KeyError(intent_id)
        if current.broker_position_ticket is None:
            return current
        if any(position.ticket == current.broker_position_ticket for position in positions):
            return current
        if current.state not in {ExecutionState.PROTECTION_VERIFIED, ExecutionState.RECONCILED}:
            return current
        related = tuple(deal for deal in deals if deal.position_id == current.broker_position_ticket)
        if not related:
            return current
        last = max(related, key=lambda deal: deal.time_msc)
        if deal_reason_sl is not None and last.reason == deal_reason_sl:
            exit_reason = "STOP_LOSS"
        elif deal_reason_tp is not None and last.reason == deal_reason_tp:
            exit_reason = "TAKE_PROFIT"
        else:
            exit_reason = "BROKER_FORCED_EXIT"
        final_pnl = sum((Decimal(str(deal.profit)) for deal in related), Decimal("0"))
        closed = current.transition(ExecutionState.CLOSED, reason=exit_reason)
        self.repository.save(closed)
        persist_trade_closure(
            self.db_path,
            intent_id=intent_id,
            requested_at_utc=now_utc,
            exit_reason=exit_reason,
            request={},
            response=None,
            outcome_class="CLOSED_BROKER_RECONCILED",
            closed_at_utc=now_utc,
            final_pnl=final_pnl,
        )
        return closed

    def _require_open_intent(self, intent_id: str) -> OrderIntent:
        intent = self.repository.get(intent_id)
        if intent is None:
            raise KeyError(intent_id)
        if intent.state not in {ExecutionState.PROTECTION_VERIFIED, ExecutionState.RECONCILED}:
            raise CloseDisarmed("close requires an open reconciled/protected intent")
        if intent.broker_position_ticket is None:
            raise CloseDisarmed("close requires broker position ticket")
        return intent

    def _assert_exit_allowed(self) -> None:
        if not self.execution_enabled:
            raise CloseDisarmed("execution_enabled=false")
        if self.identity_guard is None:
            raise CloseDisarmed("account identity guard is required")
        try:
            self.identity_guard()
        except Exception as exc:
            raise CloseDisarmed(f"account identity check failed: {exc}") from exc
