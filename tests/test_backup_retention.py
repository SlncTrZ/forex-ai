"""Backup retention & partial-file hygiene — tests for the 2026-10-22 disk-full incident.

Wing: code | Topic: backup_retention | Updated: 2026-10-22 21:45

Bối cảnh: backup DB chạy hằng ngày với retention đếm theo SỐ FILE (14) trong khi mỗi bản
~6.5GB ⇒ ~91GB, làm đầy `/` của .227 và khiến Qdrant trả 500 (No space left on device).
Ba lỗ hổng được khoá lại bằng test ở đây:
  1. retention phải có trần DUNG LƯỢNG, không chỉ đếm số file;
  2. file dở dang `*.tmp` của lần fail trước phải bị dọn (mỗi lần chạy dùng tên tmp mới
     nên code cũ không bao giờ chạm tới chúng);
  3. `-shm`/`-wal` mồ côi (base .db đã bị retention xoá) phải bị dọn.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from forex_ai.runtime import ops
from forex_ai.runtime.alerts import prune_orphan_sidecars, prune_stale_partials, retain_files_by_size


def _touch(path: Path, size: int, mtime: float) -> Path:
    path.write_bytes(b"\x00" * size)
    os.utime(path, (mtime, mtime))
    return path


# --- 1. retention theo dung lượng ------------------------------------------------


def test_retain_files_by_size_keeps_newest_within_budget(tmp_path):
    old = _touch(tmp_path / "forex-old.db", 40, 1.0)
    mid = _touch(tmp_path / "forex-mid.db", 40, 2.0)
    new = _touch(tmp_path / "forex-new.db", 40, 3.0)

    removed = retain_files_by_size(tmp_path, max_total_bytes=60, patterns=("forex-*.db",))

    assert removed == (old, mid)
    assert new.exists()
    assert not old.exists() and not mid.exists()


def test_retain_files_by_size_always_keeps_newest_even_if_oversized(tmp_path):
    old = _touch(tmp_path / "forex-old.db", 10, 1.0)
    new = _touch(tmp_path / "forex-new.db", 10, 2.0)

    removed = retain_files_by_size(tmp_path, max_total_bytes=1, patterns=("forex-*.db",))

    assert removed == (old,)
    assert new.exists()


def test_retain_files_by_size_ignores_unmatched_patterns(tmp_path):
    old = _touch(tmp_path / "forex-old.db", 10, 1.0)
    new = _touch(tmp_path / "forex-new.db", 10, 2.0)
    unrelated = _touch(tmp_path / "notes.json", 10, 3.0)

    removed = retain_files_by_size(tmp_path, max_total_bytes=1, patterns=("forex-*.db",))

    assert removed == (old,)
    assert new.exists()
    assert unrelated.exists()


def test_retain_files_by_size_rejects_non_positive_budget(tmp_path):
    with pytest.raises(ValueError):
        retain_files_by_size(tmp_path, max_total_bytes=0)


def test_retain_files_by_size_missing_directory_is_noop(tmp_path):
    assert retain_files_by_size(tmp_path / "absent", max_total_bytes=10) == ()


# --- 2. dọn file dở dang của lần chạy fail ---------------------------------------


def test_prune_stale_partials_removes_only_aged_partials(tmp_path):
    stale = _touch(tmp_path / "forex-1.db.tmp", 10, 1_000.0)
    stale_journal = _touch(tmp_path / "forex-1.db.tmp-journal", 10, 1_000.0)
    fresh = _touch(tmp_path / "forex-2.db.tmp", 10, 9_900.0)
    finished = _touch(tmp_path / "forex-3.db", 10, 1_000.0)

    removed = prune_stale_partials(
        tmp_path,
        patterns=("forex-*.db.tmp", "forex-*.db.tmp-journal"),
        min_age_seconds=600,
        now=10_000.0,
    )

    assert set(removed) == {stale, stale_journal}
    assert fresh.exists()
    assert finished.exists()


def test_prune_stale_partials_missing_directory_is_noop(tmp_path):
    assert prune_stale_partials(tmp_path / "absent", patterns=("*.tmp",), min_age_seconds=0) == ()


def test_prune_stale_partials_rejects_negative_age(tmp_path):
    with pytest.raises(ValueError):
        prune_stale_partials(tmp_path, patterns=("*.tmp",), min_age_seconds=-1)


# --- 3. dọn -shm / -wal mồ côi ---------------------------------------------------


def test_prune_orphan_sidecars_removes_sidecars_without_base(tmp_path):
    (tmp_path / "forex-1.db").write_bytes(b"x")
    orphan_shm = _touch(tmp_path / "forex-2.db-shm", 10, 1.0)
    orphan_wal = _touch(tmp_path / "forex-2.db-wal", 10, 1.0)
    kept_shm = _touch(tmp_path / "forex-1.db-shm", 10, 1.0)

    removed = prune_orphan_sidecars(tmp_path)

    assert set(removed) == {orphan_shm, orphan_wal}
    assert kept_shm.exists()


def test_prune_orphan_sidecars_missing_directory_is_noop(tmp_path):
    assert prune_orphan_sidecars(tmp_path / "absent") == ()


# --- 4. backup fail không được để lại rác ---------------------------------------


def test_backup_database_removes_partial_file_when_source_is_corrupt(tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database")
    destination = tmp_path / "backups" / "copy.db"

    with pytest.raises(sqlite3.DatabaseError):
        ops.backup_database(corrupt, destination)

    assert not destination.exists()
    assert list((tmp_path / "backups").glob("copy.db.tmp*")) == []


def test_backup_database_success_leaves_no_partial(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as con:
        con.execute("create table t (id integer primary key)")
        con.execute("insert into t default values")
        con.commit()
    destination = tmp_path / "backups" / "copy.db"

    ops.backup_database(source, destination)

    assert destination.exists()
    assert list((tmp_path / "backups").glob("copy.db.tmp*")) == []
