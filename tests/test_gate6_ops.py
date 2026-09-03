from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from forex_ai.journal.db import initialize
from forex_ai.runtime import ops

UTC = timezone.utc
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "forex.db"
    initialize(path)
    return path


def _heartbeat(path: Path, *, state: str = "HEALTHY", when: datetime = NOW) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO runtime_heartbeats(timestamp_utc,health_state,reason,payload_json) VALUES(?,?,?,?)",
            (when.isoformat(), state, "test", "{}"),
        )


def test_health_passes_with_fresh_heartbeat_and_integrity(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _heartbeat(path)
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    report = ops.assess_runtime_health(path, now_utc=NOW)
    assert report.healthy
    assert report.db_integrity == "ok"
    assert report.latest_heartbeat_state == "HEALTHY"
    assert report.unresolved_execution_intents == 0


def test_health_fails_closed_on_stale_heartbeat_low_disk_and_unknown_intent(tmp_path, monkeypatch):
    path = _db(tmp_path)
    _heartbeat(path, when=NOW - timedelta(minutes=10))
    with sqlite3.connect(path) as con:
        con.execute(
            """INSERT INTO order_intents_v1(
                intent_id,candidate_id,idempotency_key,symbol,side,volume,entry,stop_loss,take_profit,
                state,created_at_utc,filled_volume,updated_at_utc
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("i1","c1","k1","EURUSD","BUY","0.01","1","0.9","1.1","UNKNOWN",NOW.isoformat(),"0",NOW.isoformat()),
        )
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    report = ops.assess_runtime_health(path, now_utc=NOW, min_free_bytes=100)
    assert not report.healthy
    assert set(report.reasons) >= {"LOW_DISK_SPACE", "HEARTBEAT_STALE", "UNRESOLVED_EXECUTION_INTENTS"}


def test_disk_headroom_raises_before_runtime_writes(tmp_path, monkeypatch):
    path = tmp_path / "forex.db"
    monkeypatch.setattr(ops.shutil, "disk_usage", lambda _: SimpleNamespace(free=99))
    with pytest.raises(RuntimeError, match="LOW_DISK_SPACE"):
        ops.ensure_disk_headroom(path, min_free_bytes=100)


def test_backup_is_transactional_and_restore_integrity_passes(tmp_path):
    source = _db(tmp_path)
    _heartbeat(source)
    destination = tmp_path / "backups" / "copy.db"
    ops.backup_database(source, destination)
    assert destination.exists()
    assert ops.verify_database(destination) == "ok"
    with sqlite3.connect(destination) as restored:
        assert restored.execute("select count(*) from runtime_heartbeats").fetchone()[0] == 1


def test_backup_refuses_source_as_destination(tmp_path):
    source = _db(tmp_path)
    with pytest.raises(ValueError):
        ops.backup_database(source, source)
