from datetime import datetime, timezone
from pathlib import Path

from forex_ai.config import load_risk_profile
from forex_ai.execution.account_mode import MT5AccountTradeMode
from forex_ai.execution.auto_week import (
    AutoWeekApproval,
    auto_live_window,
    auto_week_expiry,
    is_auto_managed_control,
    validate_auto_week_approval,
)
from forex_ai.journal.integration_repository import TradingControlState
from forex_ai.strategy.config import bundled_strategy_snapshot

UTC = timezone.utc
APPROVAL = Path("config/live-prospective-approval.json")


def test_auto_live_window_uses_sunday_new_york_reopen():
    # 2026-09-06 Sunday: New York is UTC-4; 17:05 ET = Monday 04:05 Vietnam.
    assert not auto_live_window(datetime(2026, 9, 6, 21, 4, tzinfo=UTC))
    assert auto_live_window(datetime(2026, 9, 6, 21, 5, tzinfo=UTC))
    assert auto_live_window(datetime(2026, 9, 7, 4, 5, tzinfo=UTC))
    assert auto_live_window(datetime(2026, 9, 11, 19, 59, tzinfo=UTC))
    assert not auto_live_window(datetime(2026, 9, 11, 20, 0, tzinfo=UTC))
    assert not auto_live_window(datetime(2026, 9, 12, 12, 0, tzinfo=UTC))


def test_auto_week_expiry_is_friday_1600_new_york():
    sunday_expiry = auto_week_expiry(datetime(2026, 9, 6, 21, 5, tzinfo=UTC))
    assert sunday_expiry == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)
    expiry = auto_week_expiry(datetime(2026, 9, 8, 12, 0, tzinfo=UTC))
    assert expiry == datetime(2026, 9, 11, 20, 0, tzinfo=UTC)


def test_manual_kill_state_is_not_auto_managed():
    assert is_auto_managed_control(TradingControlState())
    assert is_auto_managed_control(TradingControlState(False, None, True, False, "AUTO_WEEKEND_DISARM"))
    assert is_auto_managed_control(TradingControlState(False, None, True, False, "AUTO_PREFLIGHT_BLOCKED:OPS_DB"))
    assert not is_auto_managed_control(TradingControlState(False, None, True, False, "MANUAL_KILL"))


def test_live_approval_matches_frozen_strategy_and_risk_profile():
    approval = AutoWeekApproval.load(APPROVAL)
    snapshot = bundled_strategy_snapshot()
    profile = load_risk_profile(Path("config/risk.yaml"))
    reasons = validate_auto_week_approval(
        approval,
        strategy_config_fingerprint=snapshot.production_fingerprint,
        symbols=("XAUUSD",),
        risk_profile=profile,
        account_trade_mode=int(MT5AccountTradeMode.REAL),
        account_identity_bound=True,
        execution_enabled=True,
    )
    assert reasons == ()


def test_live_approval_rejects_symbol_or_config_drift():
    approval = AutoWeekApproval.load(APPROVAL)
    profile = load_risk_profile(Path("config/risk.yaml"))
    reasons = validate_auto_week_approval(
        approval,
        strategy_config_fingerprint="f" * 64,
        symbols=("EURUSD",),
        risk_profile=profile,
        account_trade_mode=int(MT5AccountTradeMode.REAL),
        account_identity_bound=True,
        execution_enabled=True,
    )
    assert "STRATEGY_CONFIG_CHANGED_SINCE_APPROVAL" in reasons
    assert "APPROVED_SYMBOLS_MISMATCH" in reasons
