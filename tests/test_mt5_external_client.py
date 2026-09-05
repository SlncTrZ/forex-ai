from __future__ import annotations

from pathlib import Path

from forex_ai.config import RuntimeConfig
from forex_ai.mt5 import client as client_module
from forex_ai.mt5.client import MT5Client


class FakeConn:
    def __init__(self):
        self._config = {}
        self.executed = []
        self.closed = False

    def execute(self, code):
        self.executed.append(code)

    def eval(self, code):
        if code == "mt5.initialize()":
            return True
        if code == "mt5.last_error()":
            return (1, "ok")
        values = {
            "mt5.TIMEFRAME_M1": 1,
            "mt5.TIMEFRAME_M5": 5,
            "mt5.TIMEFRAME_M15": 15,
            "mt5.TIMEFRAME_H1": 60,
            "mt5.TIMEFRAME_H4": 240,
            "mt5.TIMEFRAME_D1": 1440,
            "mt5.POSITION_TYPE_BUY": 0,
            "mt5.POSITION_TYPE_SELL": 1,
            "mt5.ORDER_TYPE_BUY": 0,
            "mt5.ORDER_TYPE_SELL": 1,
            "mt5.SYMBOL_TRADE_MODE_DISABLED": 0,
        }
        if code in values:
            return values[code]
        if "copy_rates_from_pos" in code and "symbol_info_tick" in code:
            return {"tick": {"bid": 1.1, "ask": 1.2, "time_msc": 1}, "bars": {"M15": [], "H1": [], "H4": []}}
        raise AssertionError(code)

    def close(self):
        self.closed = True


def runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        mode="OBSERVE",
        symbols=("EURUSD",),
        db_path=Path("/tmp/test.db"),
        log_dir=Path("/tmp/logs"),
        poll_seconds=1,
        mt5_host="127.0.0.1",
        mt5_port=18812,
        mt5_ui_host="127.0.0.1",
        mt5_ui_port=8080,
        mt5_engine="external",
    )


def test_external_engine_connects_existing_bridge_without_container_manager(monkeypatch):
    fake = FakeConn()
    monkeypatch.setattr(client_module.rpyc.classic, "connect", lambda host, port: fake)
    client = MT5Client(runtime_config())
    assert client.connect()
    assert client.mt5 is None
    assert fake._config["sync_request_timeout"] == 30
    assert any("MetaTrader5 as mt5" in code for code in fake.executed)
    constants = client.constants()
    assert constants["M15"] == 15
    assert constants["SYMBOL_ORDER_MARKET"] == 1
    bundle = client.scan_bundle("EURUSDc", {"M15": 15, "H1": 60, "H4": 240}, 80)
    assert bundle["tick"]["bid"] == 1.1
    assert set(bundle["bars"]) == {"M15", "H1", "H4"}
    client.close()
    assert fake.closed
