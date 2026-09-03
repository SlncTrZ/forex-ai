#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from forex_ai.config import load_llm_config, load_runtime_config
from forex_ai.intelligence.deepseek import DeepSeekError, load_api_key
from forex_ai.journal.repository import pending_signals


def _base_symbol(actual: str, configured: tuple[str, ...]) -> str | None:
    upper = actual.upper()
    for base in configured:
        if upper.startswith(base.upper()):
            return base
    return None


def main() -> int:
    cfg = load_runtime_config()
    llm_cfg = load_llm_config()
    if not bool(llm_cfg.get("enabled", False)):
        print(json.dumps({"status": "disabled"}))
        return 0
    try:
        load_api_key()
    except DeepSeekError as exc:
        print(json.dumps({"status": "missing_api_key", "error": str(exc)}))
        return 3

    pending = pending_signals(cfg.db_path, limit=3)
    if not pending:
        print(json.dumps({"status": "ok", "reviewed": 0}))
        return 0

    review_script = Path(__file__).with_name("review_shadow.py")
    reviewed = []
    for signal in pending:
        actual = str(signal.get("symbol") or "")
        base = _base_symbol(actual, cfg.symbols)
        if base is None:
            reviewed.append({"signal_id": signal["id"], "status": "unmapped_symbol", "symbol": actual})
            continue
        cmd = [
            sys.executable,
            str(review_script),
            base,
            "--signal-id",
            str(signal["id"]),
            "--correlation-id",
            str(signal.get("signal_key") or f"signal-{signal['id']}"),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        reviewed.append(
            {
                "signal_id": signal["id"],
                "symbol": actual,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:],
                "stderr": proc.stderr[-2000:],
            }
        )
        if proc.returncode != 0:
            break

    print(json.dumps({"status": "ok", "reviewed": reviewed}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
