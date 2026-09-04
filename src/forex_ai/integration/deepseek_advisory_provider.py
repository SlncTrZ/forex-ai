from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

from forex_ai.advisory.models import ProviderResult
from forex_ai.integration.advisory_compat import legacy_review_to_provider_result
from forex_ai.intelligence.deepseek_web import DeepSeekWebReviewer
from forex_ai.strategy.v1.contracts import CandidateEnvelope


class DeepSeekLegacyAdvisoryProvider:
    """Bridge the legacy reviewer into the V1 advisory runtime with zero trade authority.

    Until a native NO_CHANGE/REDUCE_RISK/VETO provider schema is implemented, the
    legacy BUY/SELL/NO_TRADE output is intentionally collapsed to NO_CHANGE-only
    evidence by ``legacy_review_to_provider_result``.
    """

    provider_id = "deepseek-legacy-advisory-bridge"

    def __init__(self, reviewer: DeepSeekWebReviewer):
        self.reviewer = reviewer

    def review(
        self,
        candidates: Sequence[CandidateEnvelope],
        macro_context: object,
        now_utc: datetime,
    ) -> Sequence[ProviderResult]:
        del now_utc
        if not isinstance(macro_context, Mapping):
            raise TypeError("macro_context must map candidate_id to context")
        rows: list[ProviderResult] = []
        for candidate in candidates:
            context = macro_context.get(candidate.candidate_id)
            if not isinstance(context, dict):
                raise ValueError(f"missing context for candidate {candidate.candidate_id}")
            decision, usage, _raw_hash = self.reviewer.review(context)
            rows.append(
                legacy_review_to_provider_result(
                    decision,
                    model_fingerprint=self.reviewer.model,
                    cost=float(usage.api_cost_usd),
                    latency_ms=int(usage.latency_ms),
                )
            )
        return tuple(rows)
