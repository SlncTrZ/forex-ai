from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from forex_ai.intelligence.prompts import PROMPT_VERSION
from forex_ai.journal.db import SCHEMA_VERSION

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def combined_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def git_output(repo_root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo_root), *args], text=True).strip()


def assert_clean_source(repo_root: Path) -> None:
    status = git_output(repo_root, "status", "--porcelain")
    if status:
        raise RuntimeError("production release requires a clean Git working tree")


def assert_synced_with_upstream(repo_root: Path) -> None:
    upstream = git_output(repo_root, "rev-parse", "--abbrev-ref", "@{upstream}")
    ahead = git_output(repo_root, "rev-list", "--count", f"{upstream}..HEAD")
    behind = git_output(repo_root, "rev-list", "--count", f"HEAD..{upstream}")
    if ahead != "0" or behind != "0":
        raise RuntimeError(f"production release requires source synchronized with {upstream}; ahead={ahead} behind={behind}")


@dataclass(frozen=True)
class ReleaseManifest:
    git_sha: str
    config_sha256: str
    dependency_lock_sha256: str
    database_schema_version: int
    prompt_version: str
    llm_provider: str
    llm_model: str
    mt5_image: str
    release_timestamp_utc: str

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["release_fingerprint"] = self.fingerprint
        return payload


def build_release_manifest(repo_root: Path, *, now_utc: datetime | None = None) -> ReleaseManifest:
    lock = repo_root / "requirements.lock"
    config_paths = tuple(repo_root / "config" / name for name in ("app.yaml", "risk.yaml", "llm.yaml", "mt5_image.txt"))
    image_file = repo_root / "config" / "mt5_image.txt"
    missing = [str(path) for path in (lock, *config_paths) if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"release inputs missing: {missing}")
    llm = yaml.safe_load((repo_root / "config" / "llm.yaml").read_text(encoding="utf-8")) or {}
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ReleaseManifest(
        git_sha=git_output(repo_root, "rev-parse", "HEAD"),
        config_sha256=combined_sha256(config_paths),
        dependency_lock_sha256=sha256_file(lock),
        database_schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        llm_provider=str(llm.get("provider") or ""),
        llm_model=str(llm.get("model") or ""),
        mt5_image=image_file.read_text(encoding="utf-8").strip(),
        release_timestamp_utc=now.isoformat(timespec="seconds"),
    )


def write_release_manifest(repo_root: Path, output_path: Path, *, now_utc: datetime | None = None) -> ReleaseManifest:
    manifest = build_release_manifest(repo_root, now_utc=now_utc)
    output_path.write_text(json.dumps(manifest.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
