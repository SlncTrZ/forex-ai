from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, Sequence

from forex_ai.strategy.v1.contracts import CandidateEnvelope, fingerprint

from .models import ProviderResult


class AdvisoryProvider(Protocol):
    provider_id: str

    def review(self, candidates: Sequence[CandidateEnvelope], macro_context: object, now_utc: datetime) -> Sequence[ProviderResult]: ...


@dataclass(frozen=True)
class Pricing:
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float = 0.0

    def estimate(self, *, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        uncached = max(input_tokens - cached_tokens, 0)
        return (
            uncached * self.input_per_million
            + cached_tokens * self.cached_input_per_million
            + output_tokens * self.output_per_million
        ) / 1_000_000.0


@dataclass(frozen=True)
class BudgetPolicy:
    max_calls: int
    max_tokens: int
    max_cost: float


@dataclass
class BudgetState:
    calls: int = 0
    tokens: int = 0
    cost: float = 0.0

    def can_consume(self, policy: BudgetPolicy, *, calls: int, tokens: int, cost: float) -> bool:
        return self.calls + calls <= policy.max_calls and self.tokens + tokens <= policy.max_tokens and self.cost + cost <= policy.max_cost

    def consume(self, *, calls: int, tokens: int, cost: float) -> None:
        self.calls += calls
        self.tokens += tokens
        self.cost += cost


@dataclass(frozen=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    cooldown_seconds: int = 300


@dataclass
class CircuitBreaker:
    policy: CircuitBreakerPolicy
    failures: int = 0
    opened_until_utc: datetime | None = None

    def available(self, now_utc: datetime) -> bool:
        if self.opened_until_utc is None:
            return True
        if now_utc >= self.opened_until_utc:
            self.failures = 0
            self.opened_until_utc = None
            return True
        return False

    def record_success(self) -> None:
        self.failures = 0
        self.opened_until_utc = None

    def record_failure(self, now_utc: datetime) -> None:
        self.failures += 1
        if self.failures >= self.policy.failure_threshold:
            self.opened_until_utc = now_utc + timedelta(seconds=self.policy.cooldown_seconds)


def request_fingerprint(*, candidate_ids: Sequence[str], macro_cache_key: str, provider_id: str, model_id: str, config: object) -> str:
    return fingerprint({
        "candidate_ids": tuple(candidate_ids),
        "macro_cache_key": macro_cache_key,
        "provider_id": provider_id,
        "model_id": model_id,
        "config": config,
    })
