"""Owner CLI for the same guarded control service used by the dashboard.

Arming REAL accounts requires a fresh broker check and explicit approval.
No option bypasses manual emergency-stop locks.
"""
from __future__ import annotations

import argparse
import json

from forex_ai.dashboard.control import ControlDenied, change_control


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded operator trading controls")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--arm", action="store_true")
    group.add_argument("--disarm", action="store_true")
    group.add_argument("--kill", action="store_true")
    parser.add_argument("--minutes", type=int, default=60, help="ARM lease 5–1440 minutes")
    parser.add_argument("--confirm", required=True, help='Exact confirmation, e.g. "ARM DEMO"')
    args = parser.parse_args()
    action = "arm" if args.arm else "disarm" if args.disarm else "kill"
    try:
        result = change_control(action, {"minutes": args.minutes, "confirmation": args.confirm})
        print(json.dumps({"ok": True, "result": result}))
        return 0
    except ControlDenied as exc:
        print(json.dumps({"ok": False, "reason": exc.reason}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
