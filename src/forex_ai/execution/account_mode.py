from __future__ import annotations

from enum import IntEnum
from typing import Any, Mapping


class MT5AccountTradeMode(IntEnum):
    DEMO = 0
    CONTEST = 1
    REAL = 2


def require_account_trade_mode(account: Mapping[str, Any], *, expected: MT5AccountTradeMode) -> None:
    raw = account.get("trade_mode")
    if raw is None:
        raise RuntimeError("ACCOUNT_TRADE_MODE_UNAVAILABLE")
    try:
        actual = MT5AccountTradeMode(int(raw))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ACCOUNT_TRADE_MODE_INVALID") from exc
    if actual is not expected:
        raise RuntimeError(f"ACCOUNT_TRADE_MODE_MISMATCH:{actual.name}!={expected.name}")


def expected_trade_mode_for_runtime(mode: str) -> MT5AccountTradeMode:
    normalized = mode.upper()
    if normalized == "DEMO":
        return MT5AccountTradeMode.DEMO
    if normalized in {
        "LIVE_CANARY",
        "GUARDED_LIVE",
        "LIVE_EXPERIMENT",
        "CENT_GUARDED",
        "CENT_EXPERIMENT",
    }:
        return MT5AccountTradeMode.REAL
    raise RuntimeError(f"EXECUTION_MODE_NOT_TRADE_CAPABLE:{normalized}")
