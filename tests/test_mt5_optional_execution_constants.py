from __future__ import annotations

from forex_ai.mt5.client import MT5Client


def test_execution_constants_tolerate_missing_optional_constant(monkeypatch):
    required_values = {
        "TRADE_ACTION_DEAL": 1,
        "TRADE_ACTION_SLTP": 6,
        "TRADE_ACTION_REMOVE": 8,
        "ORDER_TYPE_BUY": 0,
        "ORDER_TYPE_SELL": 1,
        "ORDER_FILLING_FOK": 0,
        "ORDER_FILLING_IOC": 1,
        "ORDER_FILLING_RETURN": 2,
        "ORDER_TIME_GTC": 0,
        "TRADE_RETCODE_REQUOTE": 10004,
        "TRADE_RETCODE_REJECT": 10006,
        "TRADE_RETCODE_CANCEL": 10007,
        "TRADE_RETCODE_PLACED": 10008,
        "TRADE_RETCODE_DONE": 10009,
        "TRADE_RETCODE_DONE_PARTIAL": 10010,
        "TRADE_RETCODE_ERROR": 10011,
        "TRADE_RETCODE_TIMEOUT": 10012,
        "TRADE_RETCODE_INVALID": 10013,
        "TRADE_RETCODE_INVALID_VOLUME": 10014,
        "TRADE_RETCODE_INVALID_PRICE": 10015,
        "TRADE_RETCODE_INVALID_STOPS": 10016,
        "TRADE_RETCODE_TRADE_DISABLED": 10017,
        "TRADE_RETCODE_MARKET_CLOSED": 10018,
        "TRADE_RETCODE_NO_MONEY": 10019,
        "TRADE_RETCODE_PRICE_CHANGED": 10020,
        "TRADE_RETCODE_PRICE_OFF": 10021,
        "TRADE_RETCODE_TOO_MANY_REQUESTS": 10024,
        "TRADE_RETCODE_FROZEN": 10029,
        "TRADE_RETCODE_INVALID_FILL": 10030,
        "TRADE_RETCODE_CONNECTION": 10031,
        "TRADE_RETCODE_LIMIT_ORDERS": 10033,
        "TRADE_RETCODE_LIMIT_VOLUME": 10034,
        "TRADE_RETCODE_INVALID_ORDER": 10035,
        "TRADE_RETCODE_LIMIT_POSITIONS": 10040,
        "TRADE_RETCODE_LONG_ONLY": 10042,
        "TRADE_RETCODE_SHORT_ONLY": 10043,
        "TRADE_RETCODE_CLOSE_ONLY": 10044,
    }
    optional_values = {
        "TRADE_RETCODE_HEDGE_PROHIBITED": None,
        "TRADE_RETCODE_LOCKED": 10028,
        "DEAL_REASON_SL": 4,
        "DEAL_REASON_TP": 5,
    }

    def fake_eval(self, code: str):
        if code.startswith("mt5."):
            return required_values[code.removeprefix("mt5.")]
        name = code.split("'")[1]
        return optional_values[name]

    monkeypatch.setattr(MT5Client, "_remote_eval", fake_eval)
    client = object.__new__(MT5Client)
    values = client.execution_constants()

    assert values["TRADE_RETCODE_DONE"] == 10009
    assert values["DEAL_REASON_SL"] == 4
    assert values["DEAL_REASON_TP"] == 5
    assert values["TRADE_RETCODE_LOCKED"] == 10028
    assert "TRADE_RETCODE_HEDGE_PROHIBITED" not in values
