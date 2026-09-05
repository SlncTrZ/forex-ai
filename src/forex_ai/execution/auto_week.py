from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from forex_ai.execution.account_mode import MT5AccountTradeMode
from forex_ai.execution.live_canary import StrategyApproval
from forex_ai.journal.integration_repository import TradingControlState
from forex_ai.risk.profile import RiskProfile

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
MONDAY = 0
FRIDAY = 4
AUTO_START = time(0, 5)
ENTRY_CUTOFF = time(16, 0)
AUTO_REASON_PREFIXES = ("AUTO_WEEK_ARM:", "AUTO_WEEKEND_DISARM", "AUTO_PREFLIGHT_BLOCKED:")
AUTO_RECOVERABLE_REASONS = ("UNINITIALIZED", "AUTO_WEEKEND_DISARM")


@dataclass(frozen=True)
class AutoWeekApproval:
    strategy_approval: StrategyApproval
    symbols: tuple[str, ...]
    risk_profile: dict[str, Any]
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "AutoWeekApproval":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("schema") != "forex-ai-live-prospective-approval-v1":
            raise ValueError("unsupported live prospective approval schema")
        approval = StrategyApproval.load(path)
        symbols = tuple(str(item) for item in raw.get("symbols") or ())
        if not symbols:
            raise ValueError("live prospective approval requires symbols")
        risk_profile = raw.get("risk_profile")
        if not isinstance(risk_profile, dict):
            raise ValueError("live prospective approval requires risk_profile")
        return cls(approval, symbols, dict(risk_profile), raw)


def auto_live_window(now_utc: datetime) -> bool:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    local = now_utc.astimezone(NEW_YORK)
    weekday = local.weekday()
    clock = local.time().replace(tzinfo=None)
    if weekday == MONDAY:
        return clock >= AUTO_START
    if MONDAY < weekday < FRIDAY:
        return True
    if weekday == FRIDAY:
        return clock < ENTRY_CUTOFF
    return False


def auto_week_expiry(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    local = now_utc.astimezone(NEW_YORK)
    if local.weekday() > FRIDAY:
        raise ValueError("auto_week_expiry requires an active trading week")
    friday_date = local.date() + timedelta(days=FRIDAY - local.weekday())
    local_expiry = datetime.combine(friday_date, ENTRY_CUTOFF, tzinfo=NEW_YORK)
    return local_expiry.astimezone(UTC)


def is_auto_managed_control(state: TradingControlState) -> bool:
    return state.reason in AUTO_RECOVERABLE_REASONS or state.reason.startswith(AUTO_REASON_PREFIXES)


def validate_auto_week_approval(
    approval: AutoWeekApproval,
    *,
    strategy_config_fingerprint: str,
    symbols: tuple[str, ...],
    risk_profile: RiskProfile,
    account_trade_mode: int | None,
    account_identity_bound: bool,
    execution_enabled: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    strategy = approval.strategy_approval
    if not strategy.approved:
        reasons.append("STRATEGY_NOT_APPROVED")
    if strategy.strategy_config_fingerprint != strategy_config_fingerprint:
        reasons.append("STRATEGY_CONFIG_CHANGED_SINCE_APPROVAL")
    if len(strategy.evidence_fingerprint) != 64:
        reasons.append("STRATEGY_EVIDENCE_FINGERPRINT_INVALID")
    if tuple(symbols) != approval.symbols:
        reasons.append("APPROVED_SYMBOLS_MISMATCH")
    if not execution_enabled:
        reasons.append("EXECUTION_DISABLED")
    if account_trade_mode is None:
        reasons.append("ACCOUNT_TRADE_MODE_UNAVAILABLE")
    elif int(account_trade_mode) != int(MT5AccountTradeMode.REAL):
        reasons.append("ACCOUNT_NOT_REAL")
    if not account_identity_bound:
        reasons.append("ACCOUNT_BINDING_MISSING")

    expected = approval.risk_profile
    checks: tuple[tuple[str, Any, Any], ...] = (
        ("max_risk_per_trade_pct", risk_profile.max_risk_per_trade_pct, Decimal(str(expected.get("max_risk_per_trade_pct")))),
        ("max_total_open_risk_pct", risk_profile.max_total_open_risk_pct, Decimal(str(expected.get("max_total_open_risk_pct")))),
        ("daily_loss_limit_pct", risk_profile.daily_loss_limit_pct, Decimal(str(expected.get("daily_loss_limit_pct")))),
        ("weekly_loss_limit_pct", risk_profile.weekly_loss_limit_pct, Decimal(str(expected.get("weekly_loss_limit_pct")))),
        ("max_active_orders", risk_profile.max_active_orders, int(expected.get("max_active_orders"))),
        ("max_correlated_risk_pct", risk_profile.max_correlated_risk_pct, Decimal(str(expected.get("max_correlated_risk_pct")))),
        ("max_drawdown_pct", risk_profile.max_drawdown_pct, Decimal(str(expected.get("max_drawdown_pct")))),
    )
    for name, actual, wanted in checks:
        if actual != wanted:
            reasons.append(f"RISK_PROFILE_MISMATCH:{name}")
    if risk_profile.kill_switch:
        reasons.append("RISK_PROFILE_KILL_SWITCH")
    if not risk_profile.enabled:
        reasons.append("RISK_PROFILE_DISABLED")
    return tuple(dict.fromkeys(reasons))
