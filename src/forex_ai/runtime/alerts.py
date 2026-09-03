from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

UTC = timezone.utc


@dataclass(frozen=True)
class Alert:
    created_at_utc: str
    severity: str
    code: str
    summary: str
    context: dict[str, object]


def build_alert(*, severity: str, code: str, summary: str, context: dict[str, object] | None = None) -> Alert:
    return Alert(
        created_at_utc=datetime.now(UTC).isoformat(timespec="microseconds"),
        severity=severity,
        code=code,
        summary=summary,
        context=dict(context or {}),
    )


def spool_alert(alert: Alert, spool_dir: Path) -> Path:
    spool_dir.mkdir(parents=True, exist_ok=True)
    safe_code = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in alert.code)[:80]
    stamp = alert.created_at_utc.replace(":", "").replace("+", "_")
    path = spool_dir / f"{stamp}-{safe_code}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(alert), sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def deliver_alert(alert: Alert, *, executable: str | None, timeout_seconds: int = 10) -> bool:
    if not executable:
        return False
    payload = json.dumps(asdict(alert), sort_keys=True, ensure_ascii=False).encode("utf-8")
    completed = subprocess.run(
        [executable],
        input=payload,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_seconds,
        check=False,
    )
    return completed.returncode == 0


def retain_files(directory: Path, *, keep: int, patterns: Iterable[str] = ("*.db", "*.json")) -> tuple[Path, ...]:
    if keep < 1:
        raise ValueError("keep must be >= 1")
    files: dict[Path, float] = {}
    if not directory.exists():
        return ()
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file():
                files[path] = path.stat().st_mtime
    ordered = sorted(files, key=lambda path: (files[path], path.name), reverse=True)
    removed: list[Path] = []
    for path in ordered[keep:]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return tuple(removed)
