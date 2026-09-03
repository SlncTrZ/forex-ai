from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class ExecutionState(StrEnum):
    INTENT_CREATED = "INTENT_CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    SEND_STARTED = "SEND_STARTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"
    PROTECTION_VERIFIED = "PROTECTION_VERIFIED"
    RECONCILED = "RECONCILED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


_ALLOWED: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.INTENT_CREATED: frozenset({ExecutionState.RISK_APPROVED, ExecutionState.REJECTED}),
    ExecutionState.RISK_APPROVED: frozenset({ExecutionState.PREFLIGHT_PASSED, ExecutionState.REJECTED}),
    ExecutionState.PREFLIGHT_PASSED: frozenset({ExecutionState.SEND_STARTED, ExecutionState.REJECTED}),
    ExecutionState.SEND_STARTED: frozenset({ExecutionState.ACCEPTED, ExecutionState.REJECTED, ExecutionState.UNKNOWN}),
    ExecutionState.ACCEPTED: frozenset({ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.UNKNOWN}),
    ExecutionState.PARTIALLY_FILLED: frozenset({ExecutionState.FILLED, ExecutionState.CANCELLED, ExecutionState.UNKNOWN}),
    ExecutionState.FILLED: frozenset({ExecutionState.PROTECTION_VERIFIED, ExecutionState.UNKNOWN}),
    ExecutionState.PROTECTION_VERIFIED: frozenset({ExecutionState.RECONCILED, ExecutionState.CLOSED}),
    ExecutionState.UNKNOWN: frozenset({ExecutionState.ACCEPTED, ExecutionState.PARTIALLY_FILLED, ExecutionState.FILLED, ExecutionState.REJECTED, ExecutionState.RECONCILED}),
    ExecutionState.RECONCILED: frozenset({ExecutionState.PROTECTION_VERIFIED, ExecutionState.CLOSED, ExecutionState.CANCELLED}),
    ExecutionState.REJECTED: frozenset(),
    ExecutionState.CANCELLED: frozenset(),
    ExecutionState.CLOSED: frozenset(),
}


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    candidate_id: str
    idempotency_key: str
    symbol: str
    side: str
    volume: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    state: ExecutionState
    created_at_utc: datetime
    broker_order_ticket: int | None = None
    broker_position_ticket: int | None = None
    filled_volume: Decimal = Decimal("0")
    last_reason: str | None = None

    @staticmethod
    def derive_idempotency_key(candidate_id: str, risk_profile_fingerprint: str, safety_snapshot_fingerprint: str) -> str:
        payload = f"{candidate_id}|{risk_profile_fingerprint}|{safety_snapshot_fingerprint}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def transition(self, target: ExecutionState, *, reason: str | None = None, **changes) -> "OrderIntent":
        if target not in _ALLOWED[self.state]:
            raise ValueError(f"invalid execution transition {self.state}->{target}")
        return replace(self, state=target, last_reason=reason, **changes)


class IntentRepository(Protocol):
    def get(self, intent_id: str) -> OrderIntent | None: ...
    def get_by_idempotency_key(self, key: str) -> OrderIntent | None: ...
    def save(self, intent: OrderIntent) -> None: ...
    def all(self) -> tuple[OrderIntent, ...]: ...


class InMemoryIntentRepository:
    def __init__(self):
        self._by_id: dict[str, OrderIntent] = {}
        self._by_key: dict[str, str] = {}

    def get(self, intent_id: str) -> OrderIntent | None:
        return self._by_id.get(intent_id)

    def get_by_idempotency_key(self, key: str) -> OrderIntent | None:
        intent_id = self._by_key.get(key)
        return self._by_id.get(intent_id) if intent_id else None

    def save(self, intent: OrderIntent) -> None:
        existing_id = self._by_key.get(intent.idempotency_key)
        if existing_id is not None and existing_id != intent.intent_id:
            raise ValueError("duplicate idempotency key")
        self._by_id[intent.intent_id] = intent
        self._by_key[intent.idempotency_key] = intent.intent_id

    def all(self) -> tuple[OrderIntent, ...]:
        return tuple(self._by_id.values())
