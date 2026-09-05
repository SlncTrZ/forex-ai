from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from forex_ai.journal.integration_repository import load_trading_control
from forex_ai.execution.account_mode import MT5AccountTradeMode
from forex_ai.risk.profile import RiskProfile
from forex_ai.runtime.ops import assess_runtime_health

UTC = timezone.utc


@dataclass(frozen=True)
class StrategyApproval:
    approved: bool
    strategy_version: str
    strategy_config_fingerprint: str
    evidence_fingerprint: str
    approved_at_utc: datetime

    @classmethod
    def load(cls, path: Path) -> "StrategyApproval":
        raw = json.loads(path.read_text(encoding="utf-8"))
        approved_at = datetime.fromisoformat(str(raw["approved_at_utc"]))
        if approved_at.tzinfo is None:
            raise ValueError("strategy approval timestamp must be timezone-aware")
        return cls(
            approved=bool(raw["approved"]),
            strategy_version=str(raw["strategy_version"]),
            strategy_config_fingerprint=str(raw.get("strategy_config_fingerprint", "")),
            evidence_fingerprint=str(raw["evidence_fingerprint"]),
            approved_at_utc=approved_at.astimezone(UTC),
        )


@dataclass(frozen=True)
class LiveCanaryReadiness:
    ready: bool
    reasons: tuple[str, ...]
    mode: str
    execution_enabled: bool
    symbols: tuple[str, ...]
    risk_profile_fingerprint: str
    strategy_version: str | None
    strategy_config_fingerprint: str
    strategy_evidence_fingerprint: str | None


def assess_live_canary_readiness(
    *,
    db_path: Path,
    mode: str,
    execution_enabled: bool,
    symbols: Sequence[str],
    risk_profile: RiskProfile,
    strategy_config_fingerprint: str,
    approval_path: Path | None,
    account_trade_mode: int | None,
    account_identity_bound: bool,
    now_utc: datetime | None = None,
) -> LiveCanaryReadiness:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    reasons: list[str] = []
    if mode != "LIVE_CANARY":
        reasons.append("MODE_NOT_LIVE_CANARY")
    if not execution_enabled:
        reasons.append("EXECUTION_DISABLED")
    normalized_symbols = tuple(symbols)
    if len(normalized_symbols) != 1:
        reasons.append("LIVE_CANARY_REQUIRES_ONE_SYMBOL")
    if account_trade_mode is None:
        reasons.append("ACCOUNT_TRADE_MODE_UNAVAILABLE")
    elif int(account_trade_mode) != int(MT5AccountTradeMode.REAL):
        reasons.append("ACCOUNT_NOT_REAL")
    if not account_identity_bound:
        reasons.append("ACCOUNT_BINDING_MISSING")

    health = assess_runtime_health(db_path, now_utc=now)
    if not health.healthy:
        reasons.extend(f"OPS_{reason}" for reason in health.reasons)

    control = load_trading_control(db_path)
    if not control.armed:
        reasons.append("CONTROL_DISARMED")
    if control.kill_switch:
        reasons.append("KILL_SWITCH_ACTIVE")
    if control.maintenance_mode:
        reasons.append("MAINTENANCE_MODE")
    if control.arm_expires_at_utc is None or control.arm_expires_at_utc <= now:
        reasons.append("ARM_EXPIRED")

    approval: StrategyApproval | None = None
    if approval_path is None or not approval_path.is_file():
        reasons.append("STRATEGY_APPROVAL_MISSING")
    else:
        try:
            approval = StrategyApproval.load(approval_path)
        except Exception:
            reasons.append("STRATEGY_APPROVAL_INVALID")
        else:
            if not approval.approved:
                reasons.append("STRATEGY_NOT_APPROVED")
            if not approval.strategy_version.strip():
                reasons.append("STRATEGY_VERSION_MISSING")
            if len(approval.strategy_config_fingerprint) != 64:
                reasons.append("STRATEGY_CONFIG_FINGERPRINT_INVALID")
            elif approval.strategy_config_fingerprint != strategy_config_fingerprint:
                reasons.append("STRATEGY_CONFIG_CHANGED_SINCE_APPROVAL")
            if len(approval.evidence_fingerprint) != 64:
                reasons.append("STRATEGY_EVIDENCE_FINGERPRINT_INVALID")

    unique = tuple(dict.fromkeys(reasons))
    return LiveCanaryReadiness(
        ready=not unique,
        reasons=unique,
        mode=mode,
        execution_enabled=execution_enabled,
        symbols=normalized_symbols,
        risk_profile_fingerprint=risk_profile.fingerprint,
        strategy_version=approval.strategy_version if approval else None,
        strategy_config_fingerprint=strategy_config_fingerprint,
        strategy_evidence_fingerprint=approval.evidence_fingerprint if approval else None,
    )
