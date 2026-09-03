#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.runtime.alerts import build_alert, deliver_alert, spool_alert
from forex_ai.runtime.ops import assess_runtime_health


def main() -> int:
    cfg = load_runtime_config()
    report = assess_runtime_health(
        cfg.db_path,
        heartbeat_max_age_seconds=int(os.getenv("FOREX_AI_HEARTBEAT_MAX_AGE_SECONDS", "180")),
        min_free_bytes=int(os.getenv("FOREX_AI_MIN_FREE_BYTES", str(512 * 1024 * 1024))),
    )
    print(json.dumps(asdict(report), sort_keys=True))
    if report.healthy:
        return 0
    alert = build_alert(
        severity="CRITICAL",
        code="OPS_HEALTH_FAILED",
        summary=",".join(report.reasons) or "runtime health failed",
        context=asdict(report),
    )
    spool_dir = Path(os.getenv("FOREX_AI_ALERT_SPOOL_DIR", str(cfg.db_path.parent / "alerts"))).expanduser()
    alert_path = spool_alert(alert, spool_dir)
    executable = os.getenv("FOREX_AI_ALERT_EXECUTABLE") or None
    delivered = deliver_alert(alert, executable=executable)
    print(json.dumps({"alert_spooled": str(alert_path), "alert_delivered": delivered}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
