#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.runtime.ops import assess_runtime_health


def main() -> int:
    cfg = load_runtime_config()
    report = assess_runtime_health(
        cfg.db_path,
        heartbeat_max_age_seconds=int(os.getenv("FOREX_AI_HEARTBEAT_MAX_AGE_SECONDS", "180")),
        min_free_bytes=int(os.getenv("FOREX_AI_MIN_FREE_BYTES", str(512 * 1024 * 1024))),
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0 if report.healthy else 2


if __name__ == "__main__":
    raise SystemExit(main())
