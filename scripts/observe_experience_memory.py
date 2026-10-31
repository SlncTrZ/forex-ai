#!/usr/bin/env python3
"""Periodically find comparable *closed system trades* for recent candidates.

Research-only scheduled observer. Does not call MT5, Ollama or trade engine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize
from forex_ai.research.experience_observer import scan_recent_experiences


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    result = scan_recent_experiences(cfg.db_path, now_utc=datetime.now(timezone.utc))
    print(json.dumps({
        "component": "historical_experience_research",
        "candidates": len(result),
        "matched": sum(row["sample_size"] > 0 for row in result),
        "calibrated": False, "history_score_points": 0,
        "research_only": True,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
