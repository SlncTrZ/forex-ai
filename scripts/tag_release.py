#!/usr/bin/env python3
"""Create the next Forex-AI annotated release tag after explicit approval.

Never push implicitly, deploy implicitly, or rewrite/force-move an existing tag.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forex_ai.versioning import VersionPolicyError, assert_release_tag, validate_new_tag


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE,
        timeout=25,
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--kind", required=True, choices=("major", "small"))
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if git(repo, "symbolic-ref", "--short", "HEAD") != "main":
        raise VersionPolicyError("VERSION_RELEASE_REQUIRES_MAIN")
    if git(repo, "rev-list", "--count", "origin/main..HEAD") != "0":
        raise VersionPolicyError("VERSION_MAIN_NOT_SYNCED")
    if git(repo, "rev-list", "--count", "HEAD..origin/main") != "0":
        raise VersionPolicyError("VERSION_MAIN_BEHIND")
    pending = validate_new_tag(repo, kind=args.kind, summary=args.summary)
    msg = (f"Release {pending.next_tag}\n\n"
           f"Change-Type: {args.kind}\n"
           f"Summary: {args.summary.strip()}")
    subprocess.run(["git", "-C", str(repo), "tag", "-a", pending.next_tag,
                    "-m", msg], check=True, timeout=25)
    if assert_release_tag(repo) != pending.next_tag:
        raise VersionPolicyError("VERSION_NEW_TAG_VERIFICATION_FAILED")
    print("TAG_CREATED", pending.next_tag, "COMMIT", git(repo, "rev-parse", "--short", "HEAD"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
