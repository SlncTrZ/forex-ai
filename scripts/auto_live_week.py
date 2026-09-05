#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.config import load_execution_enabled, load_risk_profile, load_runtime_config
from forex_ai.execution.auto_week import (
    AutoWeekApproval,
    auto_live_window,
    auto_week_expiry,
    is_auto_managed_control,
    validate_auto_week_approval,
)
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.integration_repository import TradingControlState, load_trading_control, save_trading_control
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches
from forex_ai.runtime.ops import assess_runtime_health
from forex_ai.strategy.config import load_strategy_snapshot

UTC = timezone.utc


def _approval_path() -> Path:
    raw = os.getenv("FOREX_AI_STRATEGY_APPROVAL_FILE")
    if not raw:
        raise RuntimeError("FOREX_AI_STRATEGY_APPROVAL_FILE is required")
    return Path(raw).expanduser()


def _state_payload(state: TradingControlState) -> dict[str, object]:
    return {
        "armed": state.armed,
        "arm_expires_at_utc": state.arm_expires_at_utc.isoformat() if state.arm_expires_at_utc else None,
        "kill_switch": state.kill_switch,
        "maintenance_mode": state.maintenance_mode,
        "reason": state.reason,
    }


def _save_transition(db_path: Path, previous: TradingControlState, current: TradingControlState, *, event_type: str, details: dict[str, object] | None = None) -> None:
    if previous == current:
        return
    save_trading_control(db_path, current)
    log_audit_event(
        db_path,
        event_type=event_type,
        source="auto_live_week",
        payload={
            "previous": _state_payload(previous),
            "current": _state_payload(current),
            **(details or {}),
        },
    )


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    now = datetime.now(UTC)
    control = load_trading_control(cfg.db_path)

    if not auto_live_window(now):
        if control.armed or (is_auto_managed_control(control) and not control.kill_switch):
            desired = TradingControlState(
                armed=False,
                arm_expires_at_utc=None,
                kill_switch=True,
                maintenance_mode=control.maintenance_mode,
                reason="AUTO_WEEKEND_DISARM",
            )
            _save_transition(cfg.db_path, control, desired, event_type="AUTO_WEEKEND_DISARM")
            control = desired
        print(json.dumps({"status": "disarmed", "reason": "OUTSIDE_AUTO_LIVE_WINDOW", "control": _state_payload(control)}, sort_keys=True))
        return 0

    if control.maintenance_mode:
        print(json.dumps({"status": "blocked", "reasons": ["MAINTENANCE_MODE"], "control": _state_payload(control)}, sort_keys=True))
        return 0
    if control.kill_switch and not is_auto_managed_control(control):
        print(json.dumps({"status": "blocked", "reasons": ["MANUAL_KILL_SWITCH"], "control": _state_payload(control)}, sort_keys=True))
        return 0
    if not control.armed and not control.kill_switch and not is_auto_managed_control(control):
        print(json.dumps({"status": "blocked", "reasons": ["MANUAL_DISARM"], "control": _state_payload(control)}, sort_keys=True))
        return 0

    reasons: list[str] = []
    try:
        approval = AutoWeekApproval.load(_approval_path())
        strategy_snapshot = load_strategy_snapshot()
        risk_profile = load_risk_profile()
        health = assess_runtime_health(cfg.db_path, now_utc=now)
        reasons.extend(f"OPS_{reason}" for reason in health.reasons)

        client = MT5Client(cfg)
        trade_mode = None
        identity_bound = False
        if not client.connect():
            reasons.append("MT5_CONNECT_FAILED")
        else:
            try:
                account = client.account_info() or {}
                trade_mode = account.get("trade_mode")
                identity_bound = account_matches(account)
            finally:
                client.close()

        reasons.extend(validate_auto_week_approval(
            approval,
            strategy_config_fingerprint=strategy_snapshot.production_fingerprint,
            symbols=cfg.symbols,
            risk_profile=risk_profile,
            account_trade_mode=trade_mode,
            account_identity_bound=identity_bound,
            execution_enabled=load_execution_enabled(),
        ))
    except Exception as exc:
        reasons.append(f"AUTO_PREFLIGHT_EXCEPTION:{type(exc).__name__}")

    reasons = list(dict.fromkeys(reasons))
    if reasons:
        if control.armed or is_auto_managed_control(control):
            desired = TradingControlState(
                armed=False,
                arm_expires_at_utc=None,
                kill_switch=True,
                maintenance_mode=control.maintenance_mode,
                reason="AUTO_PREFLIGHT_BLOCKED:" + ",".join(reasons[:8]),
            )
            _save_transition(
                cfg.db_path,
                control,
                desired,
                event_type="AUTO_LIVE_PREFLIGHT_BLOCKED",
                details={"reasons": reasons},
            )
            control = desired
        print(json.dumps({"status": "blocked", "reasons": reasons, "control": _state_payload(control)}, sort_keys=True))
        return 0

    expiry = auto_week_expiry(now)
    policy_tag = approval.strategy_approval.strategy_config_fingerprint[:12]
    desired = TradingControlState(
        armed=True,
        arm_expires_at_utc=expiry,
        kill_switch=False,
        maintenance_mode=False,
        reason=f"AUTO_WEEK_ARM:{policy_tag}",
    )
    _save_transition(
        cfg.db_path,
        control,
        desired,
        event_type="AUTO_LIVE_WEEK_ARMED",
        details={
            "strategy_config_fingerprint": approval.strategy_approval.strategy_config_fingerprint,
            "evidence_fingerprint": approval.strategy_approval.evidence_fingerprint,
            "symbols": list(approval.symbols),
        },
    )
    print(json.dumps({"status": "armed", "control": _state_payload(desired)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
