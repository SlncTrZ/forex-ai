from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from forex_ai.journal.db import SCHEMA_VERSION
from forex_ai.release import assert_clean_source, build_release_manifest, sha256_file, write_release_manifest


UTC = timezone.utc


def test_release_manifest_is_complete_and_stable_for_fixed_inputs(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    when = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
    first = build_release_manifest(repo, now_utc=when)
    second = build_release_manifest(repo, now_utc=when)
    assert first == second
    assert first.fingerprint == second.fingerprint
    assert len(first.git_sha) == 40
    assert len(first.config_sha256) == 64
    assert len(first.dependency_lock_sha256) == 64
    assert first.database_schema_version == SCHEMA_VERSION
    assert "@sha256:" in first.mt5_image
    output = tmp_path / "release_manifest.json"
    write_release_manifest(repo, output, now_utc=when)
    payload = json.loads(output.read_text())
    assert payload["release_fingerprint"] == first.fingerprint
    assert payload["dependency_lock_sha256"] == sha256_file(repo / "requirements.lock")


def test_assert_clean_source_rejects_dirty_tree(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "x.txt").write_text("one\n")
    subprocess.run(["git", "add", "x.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    assert_clean_source(repo)
    (repo / "x.txt").write_text("two\n")
    with pytest.raises(RuntimeError, match="clean Git working tree"):
        assert_clean_source(repo)


def test_lock_contains_hashes_and_mt5_image_is_not_latest():
    repo = Path(__file__).resolve().parents[1]
    lock_lines = [line for line in (repo / "requirements.lock").read_text().splitlines() if line.strip()]
    assert lock_lines
    assert all("==" in line and "--hash=sha256:" in line for line in lock_lines)
    image = (repo / "config" / "mt5_image.txt").read_text().strip()
    assert image.startswith("lprett/mt5linux@sha256:")
    assert ":latest" not in image
