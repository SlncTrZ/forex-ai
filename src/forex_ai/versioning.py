"""Tag-first patch-only release versions for Forex-AI.

A commit is provenance, not a displayed version. Only an annotated, sequential
v0.0.N tag pointing exactly at HEAD is eligible for a production deployment.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_TAG = re.compile(r"^v0\.0\.([1-9][0-9]*)$")


class VersionPolicyError(RuntimeError):
    """Release cannot proceed because tag version policy is not satisfied."""


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE,
            timeout=12,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        raise VersionPolicyError("VERSION_GIT_UNAVAILABLE") from exc


def _tags(repo: Path) -> list[str]:
    values = _git(repo, "tag", "--list", "v0.0.*").splitlines()
    result = [item for item in values if _TAG.fullmatch(item)]
    return sorted(result, key=lambda item: int(_TAG.fullmatch(item).group(1)))


def _tag_commit(repo: Path, tag: str) -> str:
    return _git(repo, "rev-list", "-n", "1", tag)


def _tag_message(repo: Path, tag: str) -> str:
    if _git(repo, "cat-file", "-t", "refs/tags/" + tag) != "tag":
        raise VersionPolicyError("VERSION_TAG_NOT_ANNOTATED")
    body = _git(repo, "cat-file", "-p", "refs/tags/" + tag)
    return body.partition("\n\n")[2]


@dataclass(frozen=True)
class VersionEligibility:
    previous_tag: str
    next_tag: str
    small_commits: int
    major_allowed: bool


def eligibility(repo: Path) -> VersionEligibility:
    tags = _tags(repo)
    if not tags:
        raise VersionPolicyError("VERSION_BASE_TAG_MISSING")
    last = tags[-1]
    next_tag = f"v0.0.{int(_TAG.fullmatch(last).group(1)) + 1}"
    count = int(_git(repo, "rev-list", "--first-parent", "--no-merges", "--count", f"{last}..HEAD"))
    return VersionEligibility(last, next_tag, count, True)


def assert_release_tag(repo: Path) -> str:
    """Fail closed unless HEAD has the latest sequential annotated release tag."""
    tags = _tags(repo)
    if not tags:
        raise VersionPolicyError("VERSION_TAG_MISSING")
    tag = tags[-1]
    head = _git(repo, "rev-parse", "HEAD")
    if _tag_commit(repo, tag) != head:
        raise VersionPolicyError("VERSION_HEAD_NOT_TAGGED")
    version = int(_TAG.fullmatch(tag).group(1))
    if version > 1 and f"v0.0.{version - 1}" not in tags:
        raise VersionPolicyError("VERSION_TAG_SEQUENCE_GAP")
    message = _tag_message(repo, tag)
    if version >= 4:
        kind = re.search(r"(?m)^Change-Type: (major|small)$", message)
        summary = re.search(r"(?m)^Summary: (\S.+)$", message)
        if kind is None or summary is None:
            raise VersionPolicyError("VERSION_TAG_METADATA_MISSING")
        if kind.group(1) == "small":
            previous = f"v0.0.{version - 1}"
            count = int(_git(repo, "rev-list", "--first-parent", "--no-merges",
                             "--count", f"{previous}..{tag}"))
            if count < 3:
                raise VersionPolicyError("VERSION_SMALL_COMMIT_THRESHOLD")
    return tag


def release_tag_or_empty(repo: Path) -> str:
    """Development manifest may be built before tagging; deploy never may."""
    try:
        return assert_release_tag(repo)
    except VersionPolicyError:
        return ""


def validate_new_tag(repo: Path, *, kind: str, summary: str) -> VersionEligibility:
    """Called only by explicit owner-authorized release tagging."""
    if kind not in ("major", "small") or not summary.strip():
        raise VersionPolicyError("VERSION_RELEASE_REASON_REQUIRED")
    tags = _tags(repo)
    if any(_tag_commit(repo, tag) == _git(repo, "rev-parse", "HEAD") for tag in tags):
        raise VersionPolicyError("VERSION_HEAD_ALREADY_TAGGED")
    result = eligibility(repo)
    if result.small_commits < 1:
        raise VersionPolicyError("VERSION_EMPTY_RELEASE")
    if kind == "small" and result.small_commits < 3:
        raise VersionPolicyError("VERSION_SMALL_COMMIT_THRESHOLD")
    if _git(repo, "status", "--porcelain"):
        raise VersionPolicyError("VERSION_DIRTY_SOURCE")
    return result
