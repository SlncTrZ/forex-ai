from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AdvisoryAction(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    REDUCE_RISK = "REDUCE_RISK"
    VETO = "VETO"


class AdvisoryStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class AdvisoryEvidence:
    evidence_id: str
    reason_code: str
    source_backed: bool
    material_conflict: bool
    summary: str = ""


@dataclass(frozen=True)
class Advisory:
    candidate_id: str
    action: AdvisoryAction
    risk_multiplier: float
    evidence_id: str
    expires_at_utc: datetime
    model_fingerprint: str
    advisory_cost: float
    status: AdvisoryStatus = AdvisoryStatus.AVAILABLE
    reason_code: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.risk_multiplier <= 1.0:
            raise ValueError("risk_multiplier must be within 0..1")
        if self.action == AdvisoryAction.VETO and self.risk_multiplier != 0.0:
            raise ValueError("VETO must use risk_multiplier=0")
        if self.action == AdvisoryAction.NO_CHANGE and self.risk_multiplier != 1.0:
            raise ValueError("NO_CHANGE must use risk_multiplier=1")


@dataclass(frozen=True)
class ProviderResult:
    status: AdvisoryStatus
    evidence: AdvisoryEvidence | None
    suggested_action: AdvisoryAction = AdvisoryAction.NO_CHANGE
    suggested_multiplier: float = 1.0
    model_fingerprint: str = ""
    cost: float = 0.0
    latency_ms: int = 0
    error: str = ""
