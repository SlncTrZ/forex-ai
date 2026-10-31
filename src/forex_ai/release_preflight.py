"""Read-only release eligibility for the freshly connected broker account.

Never infer account mode from a configuration label. DEMO deployments must
not be blocked by unrelated REAL strategy-approval drift; REAL/UNKNOWN fail
closed without matching account binding and approved strategy fingerprint.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReleaseAccountDecision:
    permitted: bool
    reason_code: str
    mode: str


def assess_release_account(*, mode: str, bound: bool,
                           real_approval_present: bool,
                           real_approval_valid: bool,
                           real_fingerprint_matches: bool) -> ReleaseAccountDecision:
    if mode not in ("DEMO", "REAL"):
        return ReleaseAccountDecision(False, "RELEASE_ACCOUNT_MODE_UNSUPPORTED", mode)
    if not bound:
        return ReleaseAccountDecision(False, "RELEASE_ACCOUNT_UNBOUND", mode)
    if mode == "DEMO":
        return ReleaseAccountDecision(True, "RELEASE_BROKER_VERIFIED_DEMO", mode)
    if not real_approval_present:
        return ReleaseAccountDecision(False, "RELEASE_REAL_APPROVAL_MISSING", mode)
    if not real_approval_valid:
        return ReleaseAccountDecision(False, "RELEASE_REAL_APPROVAL_INVALID", mode)
    if not real_fingerprint_matches:
        return ReleaseAccountDecision(False, "RELEASE_REAL_FINGERPRINT_MISMATCH", mode)
    return ReleaseAccountDecision(True, "RELEASE_REAL_APPROVAL_VERIFIED", mode)
