from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from forex_ai.strategy.v1.contracts import CandidateEnvelope

from .models import AdvisoryStatus, ProviderResult
from .provider import AdvisoryProvider, BudgetPolicy, BudgetState, CircuitBreaker, CircuitBreakerPolicy, request_fingerprint


@dataclass(frozen=True)
class AdvisoryRuntimePolicy:
    budget: BudgetPolicy
    cache_ttl_seconds: int = 300
    circuit_breaker: CircuitBreakerPolicy = CircuitBreakerPolicy()
    max_batch_candidates: int = 16


@dataclass
class _CacheEntry:
    expires_at_utc: datetime
    results: tuple[ProviderResult, ...]


@dataclass
class AdvisoryRuntime:
    provider: AdvisoryProvider
    provider_model_id: str
    policy: AdvisoryRuntimePolicy
    budget_state: BudgetState = field(default_factory=BudgetState)
    circuit: CircuitBreaker = field(init=False)
    cache: dict[str, _CacheEntry] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.circuit = CircuitBreaker(self.policy.circuit_breaker)

    def _unavailable(self, count: int, *, reason: str) -> tuple[ProviderResult, ...]:
        return tuple(
            ProviderResult(
                status=AdvisoryStatus.UNAVAILABLE,
                evidence=None,
                model_fingerprint=self.provider_model_id,
                error=reason,
            )
            for _ in range(count)
        )

    def review_batch(
        self,
        candidates: Sequence[CandidateEnvelope],
        *,
        macro_context: object,
        macro_cache_key: str,
        now_utc: datetime,
        config_fingerprint: str,
        estimated_tokens: int,
        estimated_cost: float,
    ) -> tuple[ProviderResult, ...]:
        rows = tuple(candidates)
        if not rows:
            return ()
        if len(rows) > self.policy.max_batch_candidates:
            return self._unavailable(len(rows), reason="ADVISORY_BATCH_LIMIT")
        key = request_fingerprint(
            candidate_ids=tuple(candidate.candidate_id for candidate in rows),
            macro_cache_key=macro_cache_key,
            provider_id=self.provider.provider_id,
            model_id=self.provider_model_id,
            config=config_fingerprint,
        )
        cached = self.cache.get(key)
        if cached and now_utc < cached.expires_at_utc:
            return cached.results
        if not self.circuit.available(now_utc):
            return self._unavailable(len(rows), reason="ADVISORY_CIRCUIT_OPEN")
        if not self.budget_state.can_consume(
            self.policy.budget,
            calls=1,
            tokens=max(estimated_tokens, 0),
            cost=max(estimated_cost, 0.0),
        ):
            return self._unavailable(len(rows), reason="ADVISORY_BUDGET_EXHAUSTED")
        try:
            results = tuple(self.provider.review(rows, macro_context, now_utc))
        except Exception as exc:
            self.circuit.record_failure(now_utc)
            return self._unavailable(len(rows), reason=f"ADVISORY_PROVIDER_ERROR:{type(exc).__name__}")
        if len(results) != len(rows):
            self.circuit.record_failure(now_utc)
            return self._unavailable(len(rows), reason="ADVISORY_PROVIDER_CARDINALITY")
        actual_cost = sum(max(result.cost, 0.0) for result in results)
        self.budget_state.consume(calls=1, tokens=max(estimated_tokens, 0), cost=actual_cost)
        if all(result.status == AdvisoryStatus.AVAILABLE for result in results):
            self.circuit.record_success()
        else:
            self.circuit.record_failure(now_utc)
        self.cache[key] = _CacheEntry(
            expires_at_utc=now_utc + timedelta(seconds=self.policy.cache_ttl_seconds),
            results=results,
        )
        return results
