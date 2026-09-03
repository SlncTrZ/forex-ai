#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.runtime.ops import backup_database, verify_database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination")
    args = parser.parse_args()
    cfg = load_runtime_config()
    if args.destination:
        destination = Path(args.destination).expanduser()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = cfg.db_path.parent / "backups" / f"forex-{stamp}.db"
    result = backup_database(cfg.db_path, destination)
    print(f"backup={result}")
    print(f"integrity={verify_database(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
