from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
NONTERMINAL_EXECUTION_STATES = {
    "INTENT_CREATED", "RISK_APPROVED", "PREFLIGHT_PASSED", "SEND_STARTED",
    "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "UNKNOWN", "PROTECTION_VERIFIED",
}


@dataclass(frozen=True)
class OpsHealth:
    healthy: bool
    reasons: tuple[str, ...]
    latest_heartbeat_state: str | None
    latest_heartbeat_age_seconds: float | None
    db_integrity: str
    free_bytes: int
    unresolved_execution_intents: int


def _connect_readonly(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=5000")
    return con


def assess_runtime_health(
    db_path: Path,
    *,
    now_utc: datetime | None = None,
    heartbeat_max_age_seconds: int = 180,
    min_free_bytes: int = 512 * 1024 * 1024,
) -> OpsHealth:
    now = (now_utc or datetime.now(UTC)).astimezone(UTC)
    free_bytes = shutil.disk_usage(db_path.parent).free
    reasons: list[str] = []
    if free_bytes < min_free_bytes:
        reasons.append("LOW_DISK_SPACE")

    integrity = "missing"
    latest_state: str | None = None
    latest_age: float | None = None
    unresolved = 0
    if not db_path.exists():
        reasons.append("DB_MISSING")
    else:
        try:
            with _connect_readonly(db_path) as con:
                integrity = str(con.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity != "ok":
                    reasons.append("DB_INTEGRITY_FAILED")
                row = con.execute(
                    "SELECT health_state,timestamp_utc,reason,payload_json FROM runtime_heartbeats ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    reasons.append("HEARTBEAT_MISSING")
                else:
                    latest_state = str(row["health_state"])
                    ts = datetime.fromisoformat(str(row["timestamp_utc"])).astimezone(UTC)
                    latest_age = max(0.0, (now - ts).total_seconds())
                    if latest_age > heartbeat_max_age_seconds:
                        reasons.append("HEARTBEAT_STALE")
                    if latest_state not in {"HEALTHY", "SYNCING", "CONNECTING", "DEGRADED"}:
                        reasons.append(f"RUNTIME_STATE_{latest_state}")
                        try:
                            import json
                            payload = json.loads(str(row["payload_json"]))
                            for blocker in payload.get("blocking_reasons") or ():
                                reasons.append(str(blocker))
                        except Exception:
                            heartbeat_reason = str(row["reason"] or "")
                            if heartbeat_reason.startswith("SYNC_BLOCKED:"):
                                reasons.extend(part for part in heartbeat_reason.removeprefix("SYNC_BLOCKED:").split(",") if part)
                # External/manual broker exposure is an independent execution
                # blocker and must remain explainable even when market-data sync
                # is degraded for an unrelated symbol.
                try:
                    rows = con.execute(
                        "SELECT ticket FROM external_position_state_v1 WHERE active=1 AND (CAST(sl AS REAL)<=0 OR CAST(tp AS REAL)<=0)"
                    ).fetchall()
                    for position_row in rows:
                        reasons.append(f"UNPROTECTED_POSITION:{int(position_row['ticket'])}")
                except sqlite3.OperationalError:
                    pass

                placeholders = ",".join("?" for _ in NONTERMINAL_EXECUTION_STATES)
                unresolved = int(con.execute(
                    f"SELECT COUNT(*) FROM order_intents_v1 WHERE state IN ({placeholders})",
                    tuple(sorted(NONTERMINAL_EXECUTION_STATES)),
                ).fetchone()[0])
                if unresolved:
                    reasons.append("UNRESOLVED_EXECUTION_INTENTS")
        except sqlite3.Error:
            integrity = "unreadable"
            reasons.append("DB_UNREADABLE")

    return OpsHealth(
        healthy=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        latest_heartbeat_state=latest_state,
        latest_heartbeat_age_seconds=latest_age,
        db_integrity=integrity,
        free_bytes=free_bytes,
        unresolved_execution_intents=unresolved,
    )


def ensure_disk_headroom(db_path: Path, *, min_free_bytes: int = 512 * 1024 * 1024) -> None:
    free = shutil.disk_usage(db_path.parent).free
    if free < min_free_bytes:
        raise RuntimeError(f"LOW_DISK_SPACE:{free}:{min_free_bytes}")


def backup_database(source: Path, destination: Path) -> Path:
    if source.resolve() == destination.resolve():
        raise ValueError("backup destination must differ from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(source, timeout=10)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        result = str(dst.execute("PRAGMA integrity_check").fetchone()[0])
        if result != "ok":
            raise RuntimeError(f"BACKUP_INTEGRITY_FAILED:{result}")
        dst.commit()
    finally:
        dst.close()
        src.close()
    os.replace(tmp, destination)
    return destination


def verify_database(path: Path) -> str:
    with _connect_readonly(path) as con:
        return str(con.execute("PRAGMA integrity_check").fetchone()[0])
