from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Advisory, AdvisoryAction, AdvisoryStatus, ProviderResult


@dataclass(frozen=True)
class AdvisoryPolicy:
    version: str = "advisory-v1"
    default_ttl_seconds: int = 300
    minimum_reduction_multiplier: float = 0.25


def apply_provider_result(*, candidate_id: str, result: ProviderResult, now_utc: datetime, policy: AdvisoryPolicy) -> Advisory:
    expires = now_utc + timedelta(seconds=policy.default_ttl_seconds)
    # Failure, timeout, malformed output or exhausted budget is BOT_ONLY compatible.
    if result.status == AdvisoryStatus.UNAVAILABLE or result.evidence is None:
        return Advisory(candidate_id, AdvisoryAction.NO_CHANGE, 1.0, "", expires, result.model_fingerprint,
                        result.cost, AdvisoryStatus.UNAVAILABLE, "BOT_ONLY_FALLBACK")

    evidence = result.evidence
    action = result.suggested_action
    multiplier = min(max(result.suggested_multiplier, 0.0), 1.0)

    # VETO is privileged: it requires a source-backed material conflict.
    if action == AdvisoryAction.VETO:
        if not (evidence.source_backed and evidence.material_conflict):
            return Advisory(candidate_id, AdvisoryAction.NO_CHANGE, 1.0, evidence.evidence_id, expires,
                            result.model_fingerprint, result.cost, AdvisoryStatus.AVAILABLE,
                            "VETO_REQUIREMENTS_NOT_MET")
        multiplier = 0.0
    elif action == AdvisoryAction.REDUCE_RISK:
        multiplier = min(1.0, max(policy.minimum_reduction_multiplier, multiplier))
        if multiplier >= 1.0:
            action = AdvisoryAction.NO_CHANGE
            multiplier = 1.0
    else:
        action = AdvisoryAction.NO_CHANGE
        multiplier = 1.0

    return Advisory(candidate_id, action, multiplier, evidence.evidence_id, expires, result.model_fingerprint,
                    result.cost, AdvisoryStatus.AVAILABLE, evidence.reason_code)
