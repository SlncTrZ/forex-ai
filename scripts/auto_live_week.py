#!/usr/bin/env python3
"""Retired automatic ARM scheduler.

Operator ARM now persists identically for DEMO and REAL until explicit DISARM
or KILL. This compatibility entrypoint intentionally does not access MT5,
modify trading_control_state, or set automated kill/disarm states. The systemd
timer may still be installed on an older host: invoking it is harmless.
"""
from __future__ import annotations

import json


def main() -> int:
    print(json.dumps({"status": "disabled", "reason": "MANUAL_ARM_ONLY"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
