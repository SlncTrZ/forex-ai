from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

DEFAULT_FINGERPRINT_PATH = Path.home() / ".config" / "forex-ai" / "account_fingerprint"


def account_fingerprint(account: dict[str, Any]) -> str:
    login = str(account.get("login") or "")
    server = str(account.get("server") or "")
    currency = str(account.get("currency") or "")
    payload = f"{server}|{login}|{currency}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bind_account(account: dict[str, Any], path: Path = DEFAULT_FINGERPRINT_PATH) -> str:
    fp = account_fingerprint(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fp + "\n", encoding="utf-8")
    path.chmod(0o600)
    return fp


def account_matches(account: dict[str, Any], path: Path = DEFAULT_FINGERPRINT_PATH) -> bool:
    if not path.exists():
        return False
    expected = path.read_text(encoding="utf-8").strip()
    return bool(expected) and expected == account_fingerprint(account)
