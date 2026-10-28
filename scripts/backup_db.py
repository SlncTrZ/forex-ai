#!/usr/bin/env python3
"""Sao lưu SQLite của Forex-AI kèm ngân sách dung lượng và vệ sinh file dở dang.

Wing: code | Topic: backup_retention | Updated: 2026-10-22 21:50

Bất biến (invariant): job backup KHÔNG BAO GIỜ được làm đầy ổ đĩa của server.
Ngày 2026-10-22 điều này đã bị vi phạm: retention đếm theo SỐ FILE (14) × ~6.5GB ≈ 91GB
làm `/` của .227 đầy 100%, Qdrant trả 500 (No space left on device) và backup thất bại
giữa đường, để lại file `.tmp` 6.73GB không ai dọn.

Ba chốt chặn (đọc từ env, có default an toàn):
  FOREX_AI_BACKUP_MIN_FREE_GB          — chặn trước khi ghi nếu ổ trống thấp hơn (40)
  FOREX_AI_BACKUP_MAX_TOTAL_GB         — trần tổng dung lượng toàn bộ thư mục backup (25)
  FOREX_AI_BACKUP_RETENTION            — trần số file giữ lại (3)
  FOREX_AI_BACKUP_PARTIAL_MAX_AGE_MIN  — tuổi tối thiểu trước khi dọn `*.tmp` (90)
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.runtime.alerts import (
    prune_orphan_sidecars,
    prune_stale_partials,
    retain_files,
    retain_files_by_size,
)
from forex_ai.runtime.ops import (
    backup_database,
    ensure_disk_headroom,
    prune_database_records,
    verify_database,
)

GIB = 1024**3
DEFAULT_MIN_FREE_GIB = 40
DEFAULT_MAX_TOTAL_GIB = 25
DEFAULT_RETENTION = 3
DEFAULT_PARTIAL_MAX_AGE_MIN = 90
PARTIAL_PATTERNS = ("forex-*.db.tmp", "forex-*.db.tmp-journal")
BACKUP_PATTERNS = ("forex-*.db",)


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


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

    backup_dir = destination.parent
    backup_dir.mkdir(parents=True, exist_ok=True)

    partial_age_seconds = _env_int("FOREX_AI_BACKUP_PARTIAL_MAX_AGE_MIN", DEFAULT_PARTIAL_MAX_AGE_MIN) * 60
    partials = prune_stale_partials(backup_dir, patterns=PARTIAL_PATTERNS, min_age_seconds=partial_age_seconds)
    print(f"pruned_partials={len(partials)}")

    min_free_bytes = _env_int("FOREX_AI_BACKUP_MIN_FREE_GB", DEFAULT_MIN_FREE_GIB) * GIB
    ensure_disk_headroom(backup_dir, min_free_bytes=min_free_bytes)

    pruned = prune_database_records(cfg.db_path)
    print(f"pruned_records={pruned}")

    result = backup_database(cfg.db_path, destination)
    print(f"backup={result}")
    print(f"integrity={verify_database(result)}")

    keep = _env_int("FOREX_AI_BACKUP_RETENTION", DEFAULT_RETENTION)
    removed = retain_files(result.parent, keep=keep, patterns=BACKUP_PATTERNS)
    print(f"retention_removed={len(removed)}")

    max_total_bytes = _env_int("FOREX_AI_BACKUP_MAX_TOTAL_GB", DEFAULT_MAX_TOTAL_GIB) * GIB
    removed_by_size = retain_files_by_size(result.parent, max_total_bytes=max_total_bytes, patterns=BACKUP_PATTERNS)
    print(f"retention_removed_by_size={len(removed_by_size)}")

    orphans = prune_orphan_sidecars(result.parent)
    print(f"pruned_orphan_sidecars={len(orphans)}")

    total_bytes = sum(path.stat().st_size for path in result.parent.glob("forex-*.db"))
    print(f"backup_total_gb={total_bytes / GIB:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
