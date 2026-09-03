from __future__ import annotations

from forex_ai.advisory.models import AdvisoryAction, AdvisoryEvidence, AdvisoryStatus, ProviderResult
from forex_ai.intelligence.schemas import ReviewDecision


def legacy_review_to_provider_result(
    decision: ReviewDecision | None,
    *,
    model_fingerprint: str,
    cost: float = 0.0,
    latency_ms: int = 0,
    provider_error: str = "",
) -> ProviderResult:
    """Safely adapt the legacy BUY/SELL/NO_TRADE reviewer.

    The legacy schema cannot prove a source-backed material conflict, therefore
    it is never allowed to manufacture a VETO or a trade direction. Available
    responses become advisory evidence with NO_CHANGE; failures become UNAVAILABLE.
    """
    if decision is None:
        return ProviderResult(
            status=AdvisoryStatus.UNAVAILABLE,
            evidence=None,
            suggested_action=AdvisoryAction.NO_CHANGE,
            suggested_multiplier=1.0,
            model_fingerprint=model_fingerprint,
            cost=cost,
            latency_ms=latency_ms,
            error=provider_error or "LEGACY_REVIEW_UNAVAILABLE",
        )

    evidence = AdvisoryEvidence(
        evidence_id=f"legacy:{model_fingerprint}:{decision.action}:{decision.confidence:.4f}",
        reason_code=f"LEGACY_{decision.action}",
        source_backed=False,
        material_conflict=False,
        summary=decision.thesis,
    )
    return ProviderResult(
        status=AdvisoryStatus.AVAILABLE,
        evidence=evidence,
        suggested_action=AdvisoryAction.NO_CHANGE,
        suggested_multiplier=1.0,
        model_fingerprint=model_fingerprint,
        cost=cost,
        latency_ms=latency_ms,
    )
