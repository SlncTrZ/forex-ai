"""Arm Trading Control State — Provision or extend trading arm window for DEMO / LIVE.
Wing: code | Topic: arm_trading | Updated: 2026-10-28 14:30
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.integration_repository import TradingControlState, load_trading_control, save_trading_control

UTC = timezone.utc


def main() -> int:
    parser = argparse.ArgumentParser(description="Arm trading control state for a specified number of days.")
    parser.add_argument("--days", type=float, default=30.0, help="Số ngày kích hoạt (mặc định: 30 ngày)")
    parser.add_argument("--reason", default="DEMO_MONTH_RUN", help="Lý do kích hoạt")
    parser.add_argument("--disarm", action="store_true", help="Gỡ kích hoạt (Disarm)")
    parser.add_argument("--kill", action="store_true", help="Bật kill switch khẩn cấp")
    args = parser.parse_args()

    cfg = load_runtime_config()
    initialize(cfg.db_path)
    current = load_trading_control(cfg.db_path)
    now = datetime.now(UTC)

    if args.kill:
        new_state = TradingControlState(
            armed=False,
            arm_expires_at_utc=None,
            kill_switch=True,
            maintenance_mode=False,
            reason="MANUAL_KILL_SWITCH",
        )
    elif args.disarm:
        new_state = TradingControlState(
            armed=False,
            arm_expires_at_utc=None,
            kill_switch=False,
            maintenance_mode=False,
            reason="MANUAL_DISARM",
        )
    else:
        expiry = now + timedelta(days=args.days)
        new_state = TradingControlState(
            armed=True,
            arm_expires_at_utc=expiry,
            kill_switch=False,
            maintenance_mode=False,
            reason=args.reason,
        )

    save_trading_control(cfg.db_path, new_state)
    log_audit_event(
        cfg.db_path,
        event_type="TRADING_CONTROL_STATE_CHANGED",
        source="arm_trading",
        payload={
            "previous": {
                "armed": current.armed,
                "arm_expires_at_utc": current.arm_expires_at_utc.isoformat() if current.arm_expires_at_utc else None,
                "kill_switch": current.kill_switch,
                "reason": current.reason,
            },
            "new": {
                "armed": new_state.armed,
                "arm_expires_at_utc": new_state.arm_expires_at_utc.isoformat() if new_state.arm_expires_at_utc else None,
                "kill_switch": new_state.kill_switch,
                "reason": new_state.reason,
            },
        },
    )

    print(json.dumps({
        "status": "success",
        "armed": new_state.armed,
        "arm_expires_at_utc": new_state.arm_expires_at_utc.isoformat() if new_state.arm_expires_at_utc else None,
        "kill_switch": new_state.kill_switch,
        "reason": new_state.reason,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
