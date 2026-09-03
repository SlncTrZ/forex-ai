from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forex_ai.kernel.health import HealthState

from .db import session


@dataclass(frozen=True)
class RuntimeHeartbeat:
    timestamp_utc: datetime
    health_state: HealthState
    reason: str
    last_mt5_success_utc: datetime | None = None
    last_market_time_msc: int | None = None
    last_journal_success_utc: datetime | None = None
    payload: dict[str, Any] | None = None


def persist_heartbeat(db_path: Path, heartbeat: RuntimeHeartbeat) -> int:
    def iso(value: datetime | None) -> str | None:
        return value.astimezone(timezone.utc).isoformat() if value is not None else None

    with session(db_path) as con:
        cur = con.execute(
            """INSERT INTO runtime_heartbeats(
                timestamp_utc,health_state,reason,last_mt5_success_utc,last_market_time_msc,last_journal_success_utc,payload_json
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                iso(heartbeat.timestamp_utc), heartbeat.health_state.value, heartbeat.reason,
                iso(heartbeat.last_mt5_success_utc), heartbeat.last_market_time_msc,
                iso(heartbeat.last_journal_success_utc),
                json.dumps(heartbeat.payload or {}, ensure_ascii=False, default=str, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)
