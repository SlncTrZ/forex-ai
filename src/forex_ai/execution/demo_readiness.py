from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.journal.integration_repository import load_trading_control
from forex_ai.execution.account_mode import MT5AccountTradeMode
from forex_ai.runtime.ops import assess_runtime_health

UTC = timezone.utc


@dataclass(frozen=True)
class DemoReadiness:
    ready: bool
    reasons: tuple[str, ...]
    mode: str
    execution_enabled: bool


def assess_demo_readiness(
    *,
    db_path: Path,
    mode: str,
    execution_enabled: bool,
    account_trade_mode: int | None,
    account_identity_bound: bool,
    now_utc: datetime | None = None,
) -> DemoReadiness:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    reasons: list[str] = []
    if mode != "DEMO":
        reasons.append("MODE_NOT_DEMO")
    if not execution_enabled:
        reasons.append("EXECUTION_DISABLED")
    if account_trade_mode is None:
        reasons.append("ACCOUNT_TRADE_MODE_UNAVAILABLE")
    elif int(account_trade_mode) != int(MT5AccountTradeMode.DEMO):
        reasons.append("ACCOUNT_NOT_DEMO")
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
    if control.arm_expires_at_utc is not None and control.arm_expires_at_utc <= now:
        reasons.append("LEGACY_ARM_EXPIRED")
    unique = tuple(dict.fromkeys(reasons))
    return DemoReadiness(not unique, unique, mode, execution_enabled)
