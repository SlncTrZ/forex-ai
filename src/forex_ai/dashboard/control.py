"""Owner-gated, fail-closed dashboard controls using the existing MT5/risk/journal stack."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forex_ai.config import load_execution_enabled, load_risk_profile, load_runtime_config
from forex_ai.execution.account_mode import MT5AccountTradeMode, detect_account_trade_mode
from forex_ai.execution.auto_week import AutoWeekApproval, auto_live_window, is_auto_managed_control, validate_auto_week_approval
from forex_ai.journal.db import utc_now_parts
from forex_ai.journal.integration_repository import TradingControlState, load_trading_control
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import assert_account_matches
from forex_ai.runtime.ops import assess_runtime_health
from forex_ai.strategy.config import load_strategy_snapshot

UTC = timezone.utc


class ControlDenied(ValueError):
    def __init__(self, reason: str, status: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True)
class ArmCheck:
    account_type: str
    ready: bool
    reasons: tuple[str, ...]


def _fresh_account() -> tuple[dict, MT5AccountTradeMode]:
    cfg = load_runtime_config()
    client = MT5Client(cfg)
    try:
        if not client.connect():
            raise ControlDenied("MT5_CONNECT_FAILED")
        raw = client.account_info() or {}
        mode = detect_account_trade_mode(raw)
        if mode is MT5AccountTradeMode.CONTEST:
            raise ControlDenied("CONTEST_NOT_AUTHORIZED")
        assert_account_matches(raw, require_binding=True)
        return raw, mode
    except ControlDenied:
        raise
    except Exception as exc:
        raise ControlDenied("MT5_ACCOUNT_VERIFICATION_FAILED") from exc
    finally:
        client.close()


def arm_preflight() -> ArmCheck:
    cfg = load_runtime_config()
    now = datetime.now(UTC)
    reasons: list[str] = []
    _, mode = _fresh_account()
    control = load_trading_control(cfg.db_path)
    if not load_execution_enabled():
        reasons.append("EXECUTION_DISABLED")
    if control.maintenance_mode:
        reasons.append("MAINTENANCE_MODE")
    if control.kill_switch and not is_auto_managed_control(control):
        reasons.append("SAFETY_KILL_REQUIRES_OFFLINE_RESET")
    health = assess_runtime_health(cfg.db_path, now_utc=now)
    if not health.healthy:
        reasons.extend("OPS_" + r for r in health.reasons)
    risk = load_risk_profile()
    if not risk.enabled or risk.kill_switch:
        reasons.append("RISK_PROFILE_BLOCKED")
    if mode is MT5AccountTradeMode.REAL:
        if len(cfg.symbols) != 1:
            reasons.append("REAL_CANARY_REQUIRES_ONE_SYMBOL")
        if not auto_live_window(now):
            reasons.append("OUTSIDE_REAL_TRADING_WINDOW")
        approval_path = os.getenv("FOREX_AI_STRATEGY_APPROVAL_FILE")
        if not approval_path:
            reasons.append("REAL_APPROVAL_MISSING")
        else:
            try:
                approval = AutoWeekApproval.load(Path(approval_path).expanduser())
                snapshot = load_strategy_snapshot()
                if snapshot.loaded_from_last_good or snapshot.rejected_error:
                    reasons.append("STRATEGY_CONFIG_NOT_CURRENT")
                reasons.extend(validate_auto_week_approval(
                    approval, strategy_config_fingerprint=snapshot.production_fingerprint,
                    symbols=cfg.symbols, risk_profile=risk, account_trade_mode=int(mode),
                    account_identity_bound=True, execution_enabled=load_execution_enabled(),
                ))
            except Exception:
                reasons.append("REAL_APPROVAL_INVALID")
    return ArmCheck(mode.name, not reasons, tuple(dict.fromkeys(reasons)))


def _update_control(action: str, *, mode: str = "", minutes: int = 0) -> dict:
    db = load_runtime_config().db_path
    if not db.is_file():
        raise ControlDenied("CONTROL_DATABASE_MISSING", 503)
    now = datetime.now(UTC)
    with sqlite3.connect(db, timeout=8, isolation_level=None) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute("SELECT * FROM trading_control_state WHERE singleton=1").fetchone()
            old = (TradingControlState(bool(row["armed"]), datetime.fromisoformat(row["arm_expires_at_utc"]) if row["arm_expires_at_utc"] else None,
                        bool(row["kill_switch"]), bool(row["maintenance_mode"]), str(row["reason"])) if row else TradingControlState())
            if action == "arm":
                if old.maintenance_mode or (old.kill_switch and not is_auto_managed_control(old)):
                    raise ControlDenied("MANUAL_SAFETY_LOCK_ACTIVE")
                if old.allows_new_entries(now_utc=now):
                    raise ControlDenied("ALREADY_ARMED")
                after = TradingControlState(True, now + timedelta(minutes=minutes), False, False, f"OPERATOR_ARM_{mode}")
            elif action == "disarm":
                if not old.armed and not old.arm_expires_at_utc:
                    raise ControlDenied("ALREADY_DISARMED")
                after = TradingControlState(False, None, old.kill_switch, old.maintenance_mode, "OPERATOR_DISARM")
            elif action == "kill":
                if old.kill_switch and not old.armed:
                    raise ControlDenied("KILL_ALREADY_ACTIVE")
                after = TradingControlState(False, None, True, old.maintenance_mode, "OPERATOR_EMERGENCY_STOP")
            else:
                raise ControlDenied("INVALID_ACTION", 400)
            ts, epoch = utc_now_parts()
            con.execute(
                """INSERT INTO trading_control_state(singleton,armed,arm_expires_at_utc,kill_switch,maintenance_mode,updated_at_utc,reason)
                   VALUES (1,?,?,?,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET armed=excluded.armed,arm_expires_at_utc=excluded.arm_expires_at_utc,
                    kill_switch=excluded.kill_switch,maintenance_mode=excluded.maintenance_mode,updated_at_utc=excluded.updated_at_utc,
                    reason=excluded.reason""",
                (int(after.armed), after.arm_expires_at_utc.isoformat() if after.arm_expires_at_utc else None,
                 int(after.kill_switch), int(after.maintenance_mode), ts, after.reason),
            )
            payload = json.dumps({
                "action": action.upper(), "account_type": mode or None,
                "before": {"armed": old.armed, "kill_switch": old.kill_switch, "reason": old.reason},
                "after": {"armed": after.armed, "kill_switch": after.kill_switch, "reason": after.reason,
                          "expires_at": after.arm_expires_at_utc.isoformat() if after.arm_expires_at_utc else None},
                "actor": "authenticated_ssh_unix_socket_owner",
            })
            con.execute(
                """INSERT INTO audit_events(timestamp_utc,epoch_ms,event_type,source,payload_json)
                   VALUES(?,?,?,?,?)""", (ts, epoch, "OPERATOR_TRADING_CONTROL", "dashboard", payload),
            )
            con.commit()
        except BaseException:
            con.rollback()
            raise
    return {"action": action, "armed": after.armed, "kill_switch": after.kill_switch,
            "expires_at": after.arm_expires_at_utc.isoformat() if after.arm_expires_at_utc else None}


def change_control(action: str, payload: dict) -> dict:
    if action not in {"arm", "disarm", "kill"}:
        raise ControlDenied("INVALID_ACTION", 400)
    if not isinstance(payload, dict):
        raise ControlDenied("INVALID_PAYLOAD", 400)
    if action == "arm":
        try:
            minutes = int(payload.get("minutes"))
        except (ValueError, TypeError):
            raise ControlDenied("INVALID_LEASE_DURATION", 400) from None
        if isinstance(payload.get("minutes"), bool) or not 5 <= minutes <= 1440:
            raise ControlDenied("LEASE_MUST_BE_5_TO_1440_MINUTES", 400)
        check = arm_preflight()
        if not check.ready:
            raise ControlDenied("PREFLIGHT:" + ",".join(check.reasons))
        if payload.get("confirmation") != "ARM " + check.account_type:
            raise ControlDenied("CONFIRM_ACCOUNT_TYPE", 400)
        return _update_control("arm", mode=check.account_type, minutes=minutes)
    confirmation = "DISARM" if action == "disarm" else "EMERGENCY STOP"
    if payload.get("confirmation") != confirmation:
        raise ControlDenied("CONFIRM_" + action.upper(), 400)
    return _update_control(action)
