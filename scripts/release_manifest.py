#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from forex_ai.release import write_release_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = write_release_manifest(args.repo.resolve(), args.output.resolve())
    print(manifest.fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
