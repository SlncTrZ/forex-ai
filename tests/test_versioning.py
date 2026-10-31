"""Tag-only release identity and one-step patch sequence policy."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from forex_ai.versioning import (
    VersionPolicyError, assert_release_tag, eligibility, release_tag_or_empty,
    validate_new_tag,
)
from forex_ai.dashboard.server import _release_info


def git(repo, *args):
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        stderr=subprocess.STDOUT, text=True,
    ).strip()


def init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Release Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "file.txt").write_text("initial")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-qm", "base")
    for version in (1, 2, 3):
        git(repo, "tag", "-a", f"v0.0.{version}", "-m", "legacy tag")
    return repo


def commit(repo, n):
    (repo / "file.txt").write_text(f"change {n}")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-qm", f"fix: small change {n}")


def tag(repo, kind="small"):
    git(repo, "tag", "-a", "v0.0.4", "-m",
        "Release v0.0.4\n\nChange-Type: "+kind+"\nSummary: Verified release policy")


def test_small_changes_require_three_commits_and_exact_annotated_tag(tmp_path):
    repo = init_repo(tmp_path)
    assert eligibility(repo).next_tag == "v0.0.4"
    assert release_tag_or_empty(repo) == "v0.0.3"
    commit(repo, 1)
    assert release_tag_or_empty(repo) == ""
    commit(repo, 2)
    assert eligibility(repo).small_commits == 2
    with pytest.raises(VersionPolicyError, match="VERSION_SMALL_COMMIT_THRESHOLD"):
        validate_new_tag(repo, kind="small", summary="two fixes")
    assert validate_new_tag(repo, kind="major", summary="approved major").next_tag == "v0.0.4"
    commit(repo, 3)
    assert validate_new_tag(repo, kind="small", summary="three fixes").small_commits == 3
    tag(repo)
    assert assert_release_tag(repo) == "v0.0.4"
    assert release_tag_or_empty(repo) == "v0.0.4"
    commit(repo, 4)
    with pytest.raises(VersionPolicyError, match="VERSION_HEAD_NOT_TAGGED"):
        assert_release_tag(repo)


def test_major_change_can_release_after_one_commit_but_requires_metadata(tmp_path):
    repo = init_repo(tmp_path)
    commit(repo, 1)
    assert validate_new_tag(repo, kind="major", summary="major feature").next_tag == "v0.0.4"
    tag(repo, "major")
    assert assert_release_tag(repo) == "v0.0.4"


def test_lightweight_or_wrong_sequence_is_rejected(tmp_path):
    repo = init_repo(tmp_path)
    commit(repo, 1)
    git(repo, "tag", "v0.0.4")
    with pytest.raises(VersionPolicyError, match="VERSION_TAG_NOT_ANNOTATED"):
        assert_release_tag(repo)


def test_dashboard_reads_version_from_manifest_tag_not_release_directory(tmp_path):
    path = tmp_path / "20261031T073520Z-d16dd7c97a73" / "release_manifest.json"
    path.parent.mkdir()
    path.write_text(json.dumps({
        "release_tag": "v0.0.4",
        "git_sha": "1"*40,
        "release_fingerprint": "f"*64,
        "release_timestamp_utc": "2026-10-31T08:00:00+00:00",
    }))
    data = _release_info(path)
    assert data["version"] == "v0.0.4"
    assert data["git_sha"] == "1"*12
    assert data["fingerprint"] == "f"*12
    path.write_text(json.dumps({"git_sha": "1"*40}))
    assert _release_info(path)["version"] == "UNTAGGED"
