from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from forex_ai.execution.account_mode import (
    MT5AccountTradeMode,
    detect_account_trade_mode,
    require_account_trade_mode,
)
from forex_ai.journal.integration_repository import TradingControlState

import scripts.auto_live_week as auto_week
import scripts.execute_pending as runner

UTC = timezone.utc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(0, MT5AccountTradeMode.DEMO), (2, MT5AccountTradeMode.REAL), ("0", MT5AccountTradeMode.DEMO), ("2", MT5AccountTradeMode.REAL)],
)
def test_account_classification_comes_from_mt5(raw, expected):
    assert detect_account_trade_mode({"trade_mode": raw}) is expected


@pytest.mark.parametrize("value", [None, "garbage", 99])
def test_missing_or_invalid_mt5_mode_is_fail_closed(value):
    with pytest.raises(RuntimeError, match="ACCOUNT_TRADE_MODE_"):
        detect_account_trade_mode({} if value is None else {"trade_mode": value})


def test_switching_account_type_within_execution_is_rejected():
    with pytest.raises(RuntimeError, match="ACCOUNT_TRADE_MODE_MISMATCH"):
        require_account_trade_mode(
            {"trade_mode": MT5AccountTradeMode.REAL},
            expected=MT5AccountTradeMode.DEMO,
        )


@pytest.mark.parametrize(
    ("account_type", "runtime_mode", "expected_callee", "effective_mode"),
    [
        (MT5AccountTradeMode.DEMO, "LIVE_CANARY", "demo", "DEMO"),
        (MT5AccountTradeMode.REAL, "DEMO", "real", "LIVE_CANARY"),
        (MT5AccountTradeMode.DEMO, "OBSERVE", "demo", "DEMO"),
        (MT5AccountTradeMode.DEMO, "TRADING", "demo", "DEMO"),
    ],
)
def test_runner_routes_from_broker_not_runtime_mode(
    monkeypatch, tmp_path, account_type, runtime_mode, expected_callee, effective_mode
):
    calls = []
    def demo_stub(**kwargs):
        calls.append(("demo", kwargs))
        return SimpleNamespace(ready=False, reasons=("CONTROL_DISARMED",))

    def real_stub(**kwargs):
        calls.append(("real", kwargs))
        return SimpleNamespace(ready=False, reasons=("STRATEGY_APPROVAL_MISSING",))

    monkeypatch.setattr(runner, "assess_demo_readiness", demo_stub)
    monkeypatch.setattr(runner, "assess_live_canary_readiness", real_stub)
    monkeypatch.setattr(runner, "load_risk_profile", lambda: object())
    cfg = SimpleNamespace(mode=runtime_mode, db_path=tmp_path / "test.db", symbols=("XAUUSD",))
    outcome = runner._readiness(
        cfg,
        account_trade_mode=account_type,
        account_identity_bound=True,
        execution_enabled=True,
        strategy_config_fingerprint="a" * 64,
    )
    assert not outcome.ready
    assert len(calls) == 1
    assert calls[0][0] == expected_callee
    assert calls[0][1]["mode"] == effective_mode
    assert calls[0][1]["account_trade_mode"] == int(account_type)


def test_contest_account_cannot_use_demo_or_real_execution(monkeypatch, tmp_path):
    cfg = SimpleNamespace(mode="DEMO", db_path=tmp_path / "db", symbols=("XAUUSD",))
    with pytest.raises(RuntimeError, match="ACCOUNT_TRADE_MODE_NOT_SUPPORTED:CONTEST"):
        runner._readiness(
            cfg, account_trade_mode=MT5AccountTradeMode.CONTEST,
            account_identity_bound=True, execution_enabled=True,
            strategy_config_fingerprint="a" * 64,
        )


def test_retired_auto_week_does_not_modify_operator_control(capsys):
    # Both account types are now owner-controlled, not scheduler-controlled.
    assert auto_week.main() == 0
    output = capsys.readouterr().out
    assert '"status": "disabled"' in output
    assert "MANUAL_ARM_ONLY" in output


@pytest.mark.parametrize(
    ("account_type", "configured_mode"),
    [(MT5AccountTradeMode.DEMO, "OBSERVE"), (MT5AccountTradeMode.REAL, "DEMO")],
)
def test_execution_runner_reaches_account_gate_regardless_of_configured_mode(
    monkeypatch, tmp_path, capsys, account_type, configured_mode
):
    cfg = SimpleNamespace(mode=configured_mode, db_path=tmp_path / "db", symbols=("XAUUSD",))
    calls = []
    raw = {"trade_mode": int(account_type)}
    class Client:
        def __init__(self, cfg):
            pass
        def account_info(self):
            return raw
        def order_send(self, *args):
            pytest.fail("runner must not send orders without approved risk")

    class Coordinator:
        def __init__(self, **kwargs):
            pass
        def sync_once(self, **kwargs):
            return SimpleNamespace(ready=True, broker_state=object(), safety=object(), raw_account=raw)
        def close(self):
            pass

    def check_gate(*args, **kwargs):
        calls.append(kwargs["account_trade_mode"])
        return SimpleNamespace(ready=True, reasons=())

    monkeypatch.setattr(runner, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(runner, "initialize", lambda _: None)
    monkeypatch.setattr(runner, "load_execution_enabled", lambda: True)
    monkeypatch.setattr(runner, "load_strategy_snapshot", lambda: SimpleNamespace(production_fingerprint="a" * 64))
    monkeypatch.setattr(runner, "required_raw_bars", lambda *args, **kwargs: 0)
    monkeypatch.setattr(runner, "MT5Client", Client)
    monkeypatch.setattr(runner, "MT5ResyncCoordinator", Coordinator)
    monkeypatch.setattr(runner, "account_matches", lambda *args: True)
    monkeypatch.setattr(runner, "assert_account_matches", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_readiness", check_gate)
    monkeypatch.setattr(runner, "latest_approved_risk_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "log_audit_event", lambda *args, **kwargs: None)

    assert runner.main() == 0
    assert calls == [account_type]
    assert "NO_UNEXPIRED_APPROVED_RISK" in capsys.readouterr().out


def test_execution_runner_aborts_when_mt5_account_type_changes_mid_cycle(
    monkeypatch, tmp_path, capsys
):
    cfg = SimpleNamespace(mode="OBSERVE", db_path=tmp_path / "db", symbols=("XAUUSD",))
    class Client:
        def __init__(self, cfg):
            pass
        def account_info(self):
            return {"trade_mode": int(MT5AccountTradeMode.REAL)}
        def order_send(self, *args):
            pytest.fail("account switch must prevent execution")

    class Coordinator:
        def __init__(self, **kwargs):
            pass
        def sync_once(self, **kwargs):
            return SimpleNamespace(
                ready=True, broker_state=object(), safety=object(),
                raw_account={"trade_mode": int(MT5AccountTradeMode.DEMO)},
            )
        def close(self):
            pass

    monkeypatch.setattr(runner, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(runner, "initialize", lambda _: None)
    monkeypatch.setattr(runner, "load_execution_enabled", lambda: True)
    monkeypatch.setattr(runner, "load_strategy_snapshot", lambda: SimpleNamespace(production_fingerprint="a" * 64))
    monkeypatch.setattr(runner, "required_raw_bars", lambda *args, **kwargs: 0)
    monkeypatch.setattr(runner, "MT5Client", Client)
    monkeypatch.setattr(runner, "MT5ResyncCoordinator", Coordinator)
    monkeypatch.setattr(runner, "account_matches", lambda *args: True)
    monkeypatch.setattr(runner, "assert_account_matches", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_readiness", lambda *args, **kwargs: SimpleNamespace(ready=True, reasons=()))
    monkeypatch.setattr(runner, "log_audit_event", lambda *args, **kwargs: None)

    assert runner.main() == 7
    assert "ACCOUNT_TRADE_MODE_MISMATCH" in capsys.readouterr().out


def test_weekend_close_accepts_account_type_from_broker_not_cfg_mode(monkeypatch, tmp_path, capsys):
    import scripts.close_before_weekend as weekend
    cfg = SimpleNamespace(mode="OBSERVE", db_path=tmp_path / "db", symbols=("XAUUSD",))
    class Client:
        def __init__(self, cfg):
            pass
        def account_info(self):
            return {"trade_mode": int(MT5AccountTradeMode.DEMO)}
        def close_position(self, *args):
            pytest.fail("no managed positions; no broker order expected")

    class Coordinator:
        def __init__(self, **kwargs):
            pass
        def sync_once(self, **kwargs):
            return SimpleNamespace(
                ready=True, broker_state=SimpleNamespace(positions=(), contracts=(), ticks=()),
                raw_account={"trade_mode": int(MT5AccountTradeMode.DEMO)},
            )
        def close(self):
            pass

    monkeypatch.setattr(weekend, "weekend_force_close_due", lambda *args: True)
    monkeypatch.setattr(weekend, "load_runtime_config", lambda: cfg)
    monkeypatch.setattr(weekend, "load_execution_enabled", lambda: True)
    monkeypatch.setattr(weekend, "initialize", lambda _: None)
    monkeypatch.setattr(weekend, "load_strategy_snapshot", lambda: object())
    monkeypatch.setattr(weekend, "required_raw_bars", lambda *args, **kwargs: 0)
    monkeypatch.setattr(weekend, "MT5Client", Client)
    monkeypatch.setattr(weekend, "MT5ResyncCoordinator", Coordinator)
    monkeypatch.setattr(weekend, "assert_account_matches", lambda *args, **kwargs: None)
    monkeypatch.setattr(weekend, "SQLiteIntentRepository", lambda *args: SimpleNamespace(all=lambda: ()))
    monkeypatch.setattr(weekend, "log_audit_event", lambda *args, **kwargs: None)

    assert weekend.main() == 0
    assert "NO_MANAGED_OPEN_POSITIONS" in capsys.readouterr().out
