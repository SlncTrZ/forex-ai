from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass
from typing import Any

import rpyc
from mt5linux import MetaTrader5

from forex_ai.config import RuntimeConfig

# MetaTrader 5 SYMBOL_ORDER_MODE flag: market orders are bit 0 (value 1).
# The Python package used by mt5linux does not expose SYMBOL_ORDER_MARKET.
MT5_SYMBOL_ORDER_MARKET_BIT = 1


def plain(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "_asdict"):
        return {k: plain(v) for k, v in value._asdict().items()}
    if is_dataclass(value):
        return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


class MT5Client:
    """Thin read-first adapter around mt5linux.

    The Docker container is managed outside this class so application shutdown
    cannot accidentally remove the terminal runtime.
    """

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.mt5: MetaTrader5 | None = None
        self._external_conn: Any | None = None

    def connect(self) -> bool:
        if self.config.mt5_engine == "external":
            conn = rpyc.classic.connect(self.config.mt5_host, self.config.mt5_port)
            conn._config["sync_request_timeout"] = 30
            conn.execute("import sys; sys.path.append('C:\\\\mt5libs')")
            conn.execute("import MetaTrader5 as mt5")
            conn.execute("import datetime")
            self._external_conn = conn
            return bool(conn.eval("mt5.initialize()"))
        sink = io.StringIO()
        with redirect_stdout(sink):
            self.mt5 = MetaTrader5(
                host=self.config.mt5_host,
                port=self.config.mt5_port,
                engine=self.config.mt5_engine,
                search_on_init=False,
            )
            return bool(self.mt5.initialize())

    def close(self) -> None:
        conn = self._external_conn
        self._external_conn = None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        mt5 = self.mt5
        self.mt5 = None
        if mt5 is not None:
            try:
                mt5.shutdown()
            except Exception:
                pass

    def _require(self) -> MetaTrader5:
        if self.mt5 is None:
            raise RuntimeError("managed MT5 client is not connected")
        return self.mt5

    def version(self) -> Any:
        return plain(self._require().version())

    def _remote_eval(self, code: str) -> Any:
        if self._external_conn is not None:
            return self._external_conn.eval(code)
        mt5 = self._require()
        return mt5._container.eval(code)  # noqa: SLF001 - required workaround for mt5linux namedtuple pickling

    def terminal_info(self) -> dict[str, Any] | None:
        return plain(self._remote_eval("(lambda x: None if x is None else dict(x._asdict()))(mt5.terminal_info())"))

    def account_info(self) -> dict[str, Any] | None:
        return plain(self._remote_eval("(lambda x: None if x is None else dict(x._asdict()))(mt5.account_info())"))

    def positions(self) -> list[dict[str, Any]]:
        return plain(self._remote_eval("[dict(x._asdict()) for x in (mt5.positions_get() or ())]"))

    def history_deals(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        code = (
            "[dict(x._asdict()) for x in (mt5.history_deals_get("
            f"datetime.datetime.fromtimestamp({start_ts!r}, datetime.timezone.utc),"
            f"datetime.datetime.fromtimestamp({end_ts!r}, datetime.timezone.utc)) or ())]"
        )
        return plain(self._remote_eval(code))

    def history_orders(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        code = (
            "[dict(x._asdict()) for x in (mt5.history_orders_get("
            f"datetime.datetime.fromtimestamp({start_ts!r}, datetime.timezone.utc),"
            f"datetime.datetime.fromtimestamp({end_ts!r}, datetime.timezone.utc)) or ())]"
        )
        return plain(self._remote_eval(code))

    def symbols(self) -> list[dict[str, Any]]:
        return plain(self._remote_eval("[dict(x._asdict()) for x in (mt5.symbols_get() or ())]"))

    def symbol_names(self) -> list[dict[str, str]]:
        """Fetch only broker symbol names for strict alias resolution."""
        return plain(self._remote_eval("[{'name': x.name} for x in (mt5.symbols_get() or ())]"))

    def symbol_candidates(self, bases: tuple[str, ...]) -> list[dict[str, str]]:
        """Filter likely broker aliases remotely before crossing the RPyC boundary."""
        if not bases:
            return []
        if self._external_conn is not None:
            upper = tuple(base.upper() for base in bases)
            code = (
                "[{'name':x.name} for x in (mt5.symbols_get() or ()) "
                "if any(base in x.name.upper() for base in " + repr(upper) + ")]"
            )
            return plain(self._remote_eval(code))
        return [
            row for row in self.symbol_names()
            if any(base.upper() in str(row.get("name", "")).upper() for base in bases)
        ]

    def symbol_info(self, symbol: str) -> dict[str, Any] | None:
        return plain(self._remote_eval(f"(lambda x: None if x is None else dict(x._asdict()))(mt5.symbol_info({symbol!r}))"))

    def tick(self, symbol: str) -> dict[str, Any] | None:
        return plain(self._remote_eval(f"(lambda x: None if x is None else dict(x._asdict()))(mt5.symbol_info_tick({symbol!r}))"))

    def ticks_bundle(self, symbols: tuple[str, ...]) -> dict[str, dict[str, Any] | None]:
        """Fetch fresh ticks for the configured universe in one small round-trip."""
        if not symbols:
            return {}
        if self._external_conn is not None:
            code = (
                "{sym:(lambda x: None if x is None else {'time':x.time,'time_msc':x.time_msc,'bid':x.bid,'ask':x.ask})"
                "(mt5.symbol_info_tick(sym)) for sym in " + repr(tuple(symbols)) + "}"
            )
            return plain(self._remote_eval(code))
        return {symbol: self.tick(symbol) for symbol in symbols}

    def scan_universe_bundle(
        self,
        symbols: tuple[str, ...],
        timeframes: dict[str, int],
        count: int = 51,
    ) -> dict[str, dict[str, Any]]:
        """Fetch info, tick and bars for all configured symbols in one round-trip."""
        if not symbols:
            return {}
        if self._external_conn is not None:
            tf_items = ",".join(f"{label!r}:{int(value)!r}" for label, value in timeframes.items())
            info_fields = (
                "name", "digits", "point", "trade_contract_size", "trade_tick_size",
                "volume_min", "volume_max", "volume_step", "trade_stops_level", "trade_freeze_level",
                "trade_mode", "order_mode", "filling_mode", "currency_base", "currency_profit", "currency_margin",
            )
            code = (
                "(lambda syms,tfs,info_fields: {sym: {"
                "'info': (lambda x: None if x is None else {k:getattr(x,k) for k in info_fields})(mt5.symbol_info(sym)),"
                "'tick': (lambda x: None if x is None else {'time':x.time,'time_msc':x.time_msc,'bid':x.bid,'ask':x.ask})(mt5.symbol_info_tick(sym)),"
                "'bars': {label: (lambda rates: [] if rates is None else ["
                "{'time':int(row['time']),'open':float(row['open']),'high':float(row['high']),'low':float(row['low']),"
                "'close':float(row['close']),'tick_volume':int(row['tick_volume'])} for row in rates])"
                "(mt5.copy_rates_from_pos(sym,tf,0," + repr(int(count)) + ")) for label,tf in tfs.items()}"
                "} for sym in syms})(" + repr(tuple(symbols)) + ",{" + tf_items + "}," + repr(info_fields) + ")"
            )
            return plain(self._remote_eval(code))
        return {
            symbol: {
                "info": self.symbol_info(symbol),
                "tick": self.tick(symbol),
                "bars": {label: self.bars(symbol, timeframe, count) for label, timeframe in timeframes.items()},
            }
            for symbol in symbols
        }

    def scan_bundle(self, symbol: str, timeframes: dict[str, int], count: int = 80) -> dict[str, Any]:
        """Fetch tick plus multiple timeframe bars in one remote round-trip.

        This keeps live candidate scans close to candle close instead of paying
        one RPyC request per timeframe. Managed mode falls back to local calls.
        """
        if self._external_conn is not None:
            tf_items = ",".join(f"{label!r}:{int(value)!r}" for label, value in timeframes.items())
            code = (
                "(lambda tick,tfs: {"
                "'tick': None if tick is None else dict(tick._asdict()),"
                "'bars': {label: (lambda rates: [] if rates is None else ["
                "{name: (row[name].item() if hasattr(row[name], 'item') else row[name]) for name in rates.dtype.names} "
                "for row in rates])(mt5.copy_rates_from_pos(" + repr(symbol) + ",tf,0," + repr(int(count)) + ")) "
                "for label,tf in tfs.items()}"
                "})(mt5.symbol_info_tick(" + repr(symbol) + "),{" + tf_items + "})"
            )
            return plain(self._remote_eval(code))
        return {
            "tick": self.tick(symbol),
            "bars": {label: self.bars(symbol, timeframe, count) for label, timeframe in timeframes.items()},
        }

    def bars(self, symbol: str, timeframe: int, count: int = 100, start_pos: int = 0) -> list[dict[str, Any]]:
        if self._external_conn is not None:
            code = (
                "(lambda rates: [] if rates is None else ["
                "{name: (row[name].item() if hasattr(row[name], 'item') else row[name]) for name in rates.dtype.names} "
                f"for row in rates])(mt5.copy_rates_from_pos({symbol!r},{int(timeframe)!r},{int(start_pos)!r},{int(count)!r}))"
            )
            return plain(self._remote_eval(code))
        rates = self._require().copy_rates_from_pos(symbol, timeframe, start_pos, count)
        if rates is None:
            return []
        names = getattr(getattr(rates, "dtype", None), "names", None)
        if names:
            return [{name: plain(row[name].item() if hasattr(row[name], "item") else row[name]) for name in names} for row in rates]
        return plain(rates)

    def active_orders(self) -> list[dict[str, Any]]:
        return plain(self._remote_eval("[dict(x._asdict()) for x in (mt5.orders_get() or ())]"))

    def order_calc_profit(
        self,
        order_type: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        code = (
            "mt5.order_calc_profit("
            f"{int(order_type)!r},{symbol!r},{float(volume)!r},{float(price_open)!r},{float(price_close)!r})"
        )
        value = self._remote_eval(code)
        return None if value is None else float(value)

    def order_calc_margin(self, order_type: int, symbol: str, volume: float, price: float) -> float | None:
        code = f"mt5.order_calc_margin({int(order_type)!r},{symbol!r},{float(volume)!r},{float(price)!r})"
        value = self._remote_eval(code)
        return None if value is None else float(value)

    def order_check(self, request: dict[str, Any]) -> dict[str, Any] | None:
        code = f"(lambda x: None if x is None else dict(x._asdict()))(mt5.order_check({request!r}))"
        return plain(self._remote_eval(code))

    def order_send(self, request: dict[str, Any]) -> dict[str, Any] | None:
        code = f"(lambda x: None if x is None else dict(x._asdict()))(mt5.order_send({request!r}))"
        return plain(self._remote_eval(code))

    def update_protection(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Submit a caller-built SL/TP modification request; no request is invented here."""
        return self.order_send(request)

    def cancel_order(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Submit a caller-built pending-order cancellation request."""
        return self.order_send(request)

    def close_position(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Submit a caller-built position-close request."""
        return self.order_send(request)

    def last_error(self) -> Any:
        return plain(self._remote_eval("mt5.last_error()"))

    def constants(self) -> dict[str, int]:
        names = (
            "TIMEFRAME_M1", "TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_H1", "TIMEFRAME_H4",
            "POSITION_TYPE_BUY", "POSITION_TYPE_SELL", "ORDER_TYPE_BUY", "ORDER_TYPE_SELL",
            "SYMBOL_TRADE_MODE_DISABLED",
        )
        values = {name: int(self._remote_eval(f"mt5.{name}")) for name in names}
        return {
            "M1": values["TIMEFRAME_M1"],
            "M5": values["TIMEFRAME_M5"],
            "M15": values["TIMEFRAME_M15"],
            "H1": values["TIMEFRAME_H1"],
            "H4": values["TIMEFRAME_H4"],
            "POSITION_TYPE_BUY": values["POSITION_TYPE_BUY"],
            "POSITION_TYPE_SELL": values["POSITION_TYPE_SELL"],
            "ORDER_TYPE_BUY": values["ORDER_TYPE_BUY"],
            "ORDER_TYPE_SELL": values["ORDER_TYPE_SELL"],
            "SYMBOL_TRADE_MODE_DISABLED": values["SYMBOL_TRADE_MODE_DISABLED"],
            "SYMBOL_ORDER_MARKET": MT5_SYMBOL_ORDER_MARKET_BIT,
        }

    def execution_constants(self) -> dict[str, int]:
        names = (
            "TRADE_ACTION_DEAL",
            "TRADE_ACTION_SLTP",
            "TRADE_ACTION_REMOVE",
            "ORDER_TYPE_BUY",
            "ORDER_TYPE_SELL",
            "ORDER_FILLING_FOK",
            "ORDER_FILLING_IOC",
            "ORDER_FILLING_RETURN",
            "ORDER_TIME_GTC",
            "TRADE_RETCODE_REQUOTE",
            "TRADE_RETCODE_REJECT",
            "TRADE_RETCODE_CANCEL",
            "TRADE_RETCODE_PLACED",
            "TRADE_RETCODE_DONE",
            "TRADE_RETCODE_DONE_PARTIAL",
            "TRADE_RETCODE_ERROR",
            "TRADE_RETCODE_TIMEOUT",
            "TRADE_RETCODE_INVALID",
            "TRADE_RETCODE_INVALID_VOLUME",
            "TRADE_RETCODE_INVALID_PRICE",
            "TRADE_RETCODE_INVALID_STOPS",
            "TRADE_RETCODE_TRADE_DISABLED",
            "TRADE_RETCODE_MARKET_CLOSED",
            "TRADE_RETCODE_NO_MONEY",
            "TRADE_RETCODE_PRICE_CHANGED",
            "TRADE_RETCODE_PRICE_OFF",
            "TRADE_RETCODE_TOO_MANY_REQUESTS",
            "TRADE_RETCODE_FROZEN",
            "TRADE_RETCODE_INVALID_FILL",
            "TRADE_RETCODE_CONNECTION",
            "TRADE_RETCODE_LIMIT_ORDERS",
            "TRADE_RETCODE_LIMIT_VOLUME",
            "TRADE_RETCODE_INVALID_ORDER",
            "TRADE_RETCODE_LIMIT_POSITIONS",
            "TRADE_RETCODE_LONG_ONLY",
            "TRADE_RETCODE_SHORT_ONLY",
            "TRADE_RETCODE_CLOSE_ONLY",
            "TRADE_RETCODE_HEDGE_PROHIBITED",
            "DEAL_REASON_SL",
            "DEAL_REASON_TP",
        )
        return {name: int(self._remote_eval(f"mt5.{name}")) for name in names}
