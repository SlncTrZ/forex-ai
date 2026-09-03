from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from forex_ai.mt5.contracts import BrokerState, SafetySnapshot


class HealthState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    SYNCING = "SYNCING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class HealthTransition:
    state: HealthState
    reason: str


class BackoffPolicy:
    def __init__(self, base_seconds: float = 0.5, max_seconds: float = 30.0, jitter_ratio: float = 0.2, rng: Callable[[], float] | None = None):
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds
        self.jitter_ratio = jitter_ratio
        self.rng = rng or random.random

    def delay(self, attempt: int) -> float:
        raw = min(self.max_seconds, self.base_seconds * (2 ** max(0, attempt)))
        jitter = raw * self.jitter_ratio * ((self.rng() * 2) - 1)
        return max(0.0, raw + jitter)


class HealthKernel:
    def __init__(self):
        self.state = HealthState.DISCONNECTED
        self.baseline_account_fp: str | None = None
        self.baseline_contracts_fp: str | None = None

    def begin_connect(self) -> HealthTransition:
        self.state = HealthState.CONNECTING
        return HealthTransition(self.state, "CONNECTING")

    def connection_failed(self) -> HealthTransition:
        self.state = HealthState.DISCONNECTED
        return HealthTransition(self.state, "CONNECTION_FAILED")

    def begin_sync(self) -> HealthTransition:
        if self.state not in {HealthState.CONNECTING, HealthState.DEGRADED, HealthState.DISCONNECTED}:
            raise ValueError(f"cannot sync from {self.state}")
        self.state = HealthState.SYNCING
        return HealthTransition(self.state, "SYNCING")

    @staticmethod
    def contracts_fingerprint(state: BrokerState) -> str:
        payload = [c.model_dump(mode="json") for c in sorted(state.contracts, key=lambda x: x.symbol)]
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def complete_sync(self, broker_state: BrokerState) -> SafetySnapshot:
        account_fp = broker_state.account.identity_fingerprint
        contracts_fp = self.contracts_fingerprint(broker_state)
        blocking: list[str] = []

        if self.baseline_account_fp is None:
            self.baseline_account_fp = account_fp
        elif self.baseline_account_fp != account_fp:
            blocking.append("ACCOUNT_IDENTITY_DRIFT")

        if self.baseline_contracts_fp is None:
            self.baseline_contracts_fp = contracts_fp
        elif self.baseline_contracts_fp != contracts_fp:
            blocking.append("BROKER_CONTRACT_DRIFT")

        if any(p.sl <= 0 for p in broker_state.positions):
            blocking.append("UNPROTECTED_POSITION")

        self.state = HealthState.BLOCKED if blocking else HealthState.HEALTHY
        return SafetySnapshot(
            account_fingerprint=account_fp,
            contracts_fingerprint=contracts_fp,
            reconciled=not blocking,
            blocking_reasons=tuple(blocking),
            captured_at_utc=broker_state.reconciled_at_utc,
        )

    def degrade(self, reason: str = "BROKER_DEGRADED") -> HealthTransition:
        if self.state is not HealthState.BLOCKED:
            self.state = HealthState.DEGRADED
        return HealthTransition(self.state, reason)

    def block(self, reason: str) -> HealthTransition:
        self.state = HealthState.BLOCKED
        return HealthTransition(self.state, reason)
