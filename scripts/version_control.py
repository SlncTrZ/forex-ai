#!/usr/bin/env python3
"""Inspect the next Forex-AI tag; never create/push tags implicitly."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from forex_ai.versioning import VersionPolicyError, eligibility, release_tag_or_empty


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        state = eligibility(args.repo.resolve())
    except VersionPolicyError as exc:
        print(json.dumps({"eligible": False, "reason": str(exc)}))
        return 2
    print(json.dumps({
        "current_release_tag": release_tag_or_empty(args.repo.resolve()) or None,
        "previous_tag": state.previous_tag,
        "next_tag": state.next_tag,
        "small_commits_since_previous_tag": state.small_commits,
        "small_bump_eligible": state.small_commits >= 3,
        "major_change_bump": "requires explicit owner approval",
        "tag_creation": "manual annotated tag followed by push",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
