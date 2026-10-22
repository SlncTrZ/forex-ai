from __future__ import annotations

import json
import os
import subprocess
import time
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


def _collect_files(directory: Path, patterns: Iterable[str]) -> dict[Path, float]:
    """Map path -> mtime cho mọi file khớp patterns. Thư mục không tồn tại -> rỗng."""
    files: dict[Path, float] = {}
    if not directory.exists():
        return files
    for pattern in patterns:
        for path in directory.glob(pattern):
            if path.is_file():
                files[path] = path.stat().st_mtime
    return files


def _newest_first(files: dict[Path, float]) -> list[Path]:
    return sorted(files, key=lambda path: (files[path], path.name), reverse=True)


def retain_files(directory: Path, *, keep: int, patterns: Iterable[str] = ("*.db", "*.json")) -> tuple[Path, ...]:
    if keep < 1:
        raise ValueError("keep must be >= 1")
    ordered = _newest_first(_collect_files(directory, patterns))
    removed: list[Path] = []
    for path in ordered[keep:]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return tuple(removed)


def retain_files_by_size(
    directory: Path, *, max_total_bytes: int, patterns: Iterable[str] = ("*.db", "*.json")
) -> tuple[Path, ...]:
    """Giữ các file mới nhất sao cho tổng dung lượng <= max_total_bytes.

    Invariant: LUÔN giữ lại file mới nhất, kể cả khi bản thân nó đã vượt ngân sách —
    thà vượt trần còn hơn mất sạch điểm phục hồi cuối cùng.
    Xoá theo thứ tự cũ nhất trước để ưu tiên giữ bản gần hiện tại.
    """
    if max_total_bytes < 1:
        raise ValueError("max_total_bytes must be >= 1")
    ordered = _newest_first(_collect_files(directory, patterns))
    total = sum(path.stat().st_size for path in ordered)
    removed: list[Path] = []
    for path in reversed(ordered[1:]):
        if total <= max_total_bytes:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        removed.append(path)
        total -= size
    return tuple(removed)


def prune_stale_partials(
    directory: Path,
    *,
    patterns: Iterable[str],
    min_age_seconds: int,
    now: float | None = None,
) -> tuple[Path, ...]:
    """Xoá file dở dang (v.d `*.tmp`) cũ hơn min_age_seconds.

    Vì mỗi lần backup dùng tên tmp mới (đính timestamp), phần dở dang của lần fail
    trước không bao giờ được lần sau chạm tới nếu không có bước dọn riêng này.
    """
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be >= 0")
    reference = time.time() if now is None else now
    removed: list[Path] = []
    for path, mtime in _collect_files(directory, patterns).items():
        if reference - mtime >= min_age_seconds:
            path.unlink(missing_ok=True)
            removed.append(path)
    return tuple(removed)


def prune_orphan_sidecars(
    directory: Path, *, suffixes: tuple[str, ...] = ("-shm", "-wal")
) -> tuple[Path, ...]:
    """Xoá `*.db-shm` / `*.db-wal` khi file gốc (.db) không còn.

    SQLite tạo sidecar khi mở DB ở chế độ WAL; retention chỉ khớp `*.db` nên sidecar
    của bản đã bị xoá sẽ nằm lại vĩnh viễn.
    """
    removed: list[Path] = []
    if not directory.exists():
        return ()
    for suffix in suffixes:
        for path in directory.glob(f"*{suffix}"):
            if not path.is_file():
                continue
            base = path.with_name(path.name[: -len(suffix)])
            if not base.exists():
                path.unlink(missing_ok=True)
                removed.append(path)
    return tuple(removed)
