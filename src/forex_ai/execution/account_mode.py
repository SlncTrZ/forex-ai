from __future__ import annotations

from enum import IntEnum
from typing import Any, Mapping


class MT5AccountTradeMode(IntEnum):
    DEMO = 0
    CONTEST = 1
    REAL = 2


def detect_account_trade_mode(account: Mapping[str, Any]) -> MT5AccountTradeMode:
    """Classify the connected broker account, never a configured runtime mode."""
    raw = account.get("trade_mode")
    if raw is None:
        raise RuntimeError("ACCOUNT_TRADE_MODE_UNAVAILABLE")
    try:
        return MT5AccountTradeMode(int(raw))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ACCOUNT_TRADE_MODE_INVALID") from exc


def require_account_trade_mode(account: Mapping[str, Any], *, expected: MT5AccountTradeMode) -> None:
    actual = detect_account_trade_mode(account)
    if actual is not expected:
        raise RuntimeError(f"ACCOUNT_TRADE_MODE_MISMATCH:{actual.name}!={expected.name}")
