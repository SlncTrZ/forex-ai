from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.journal.integration_repository import load_trading_control
from forex_ai.runtime.ops import assess_runtime_health

UTC = timezone.utc


@dataclass(frozen=True)
class DemoCampaignReadiness:
    ready: bool
    reasons: tuple[str, ...]
    campaign_id: str
    mode: str
    execution_enabled: bool


def assess_demo_campaign_readiness(
    *,
    db_path: Path,
    mode: str,
    execution_enabled: bool,
    campaign_id: str,
    now_utc: datetime | None = None,
) -> DemoCampaignReadiness:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    reasons: list[str] = []
    if mode != "DEMO":
        reasons.append("MODE_NOT_DEMO")
    if not execution_enabled:
        reasons.append("EXECUTION_DISABLED")
    if not campaign_id.strip():
        reasons.append("CAMPAIGN_ID_REQUIRED")
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
    unique = tuple(dict.fromkeys(reasons))
    return DemoCampaignReadiness(not unique, unique, campaign_id, mode, execution_enabled)
