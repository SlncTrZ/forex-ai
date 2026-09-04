from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any, Mapping

DEFAULT_FINGERPRINT_PATH = Path.home() / ".config" / "forex-ai" / "account_fingerprint"


class AccountBindingError(RuntimeError):
    """Raised when persistent account identity cannot be trusted."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def account_fingerprint(account: Mapping[str, Any]) -> str:
    login = str(account.get("login") or "")
    server = str(account.get("server") or "")
    currency = str(account.get("currency") or "")
    if not login or not server or not currency:
        raise AccountBindingError("ACCOUNT_IDENTITY_UNAVAILABLE")
    payload = f"{server}|{login}|{currency}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bind_account(account: Mapping[str, Any], path: Path = DEFAULT_FINGERPRINT_PATH) -> str:
    """Explicitly bind one broker account identity.

    Production runtime never calls this automatically. It is an owner-controlled
    provisioning operation so a process restart cannot silently trust whichever
    account MT5 happens to expose first.
    """
    fp = account_fingerprint(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fp + "\n", encoding="utf-8")
    path.chmod(0o600)
    return fp


def load_bound_fingerprint(path: Path = DEFAULT_FINGERPRINT_PATH) -> str | None:
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip().lower()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise AccountBindingError("ACCOUNT_BINDING_INVALID")
    return value


def account_matches(account: Mapping[str, Any], path: Path = DEFAULT_FINGERPRINT_PATH) -> bool:
    try:
        expected = load_bound_fingerprint(path)
        if expected is None:
            return False
        actual = account_fingerprint(account)
    except AccountBindingError:
        return False
    return hmac.compare_digest(expected, actual)


def assert_account_matches(
    account: Mapping[str, Any],
    *,
    path: Path = DEFAULT_FINGERPRINT_PATH,
    require_binding: bool = True,
) -> str | None:
    """Verify persistent owner-bound identity and return its fingerprint.

    Read-only OBSERVE callers may use ``require_binding=False`` so a not-yet-bound
    research installation remains observable. Once a binding exists, mismatch is
    always fatal. Execution-capable callers must keep ``require_binding=True``.
    """
    expected = load_bound_fingerprint(path)
    if expected is None:
        if require_binding:
            raise AccountBindingError("ACCOUNT_BINDING_MISSING")
        return None
    actual = account_fingerprint(account)
    if not hmac.compare_digest(expected, actual):
        raise AccountBindingError("ACCOUNT_IDENTITY_MISMATCH")
    return expected
