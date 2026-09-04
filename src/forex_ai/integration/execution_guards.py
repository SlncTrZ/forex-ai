from __future__ import annotations

from collections.abc import Callable

from forex_ai.risk.account_guard import AccountBindingError, assert_account_matches


def mt5_account_identity_guard(account_info: Callable[[], dict | None]) -> Callable[[], None]:
    """Build a fail-closed execution identity guard around a live MT5 account read."""

    def guard() -> None:
        account = account_info()
        if not account:
            raise AccountBindingError("ACCOUNT_IDENTITY_UNAVAILABLE")
        assert_account_matches(account, require_binding=True)

    return guard
