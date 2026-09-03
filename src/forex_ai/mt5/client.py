from __future__ import annotations

import io
from contextlib import redirect_stdout
from dataclasses import asdict, is_dataclass
from typing import Any

from mt5linux import MetaTrader5

from forex_ai.config import RuntimeConfig


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

    def connect(self) -> bool:
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
        if self.mt5 is not None:
            try:
                self.mt5.shutdown()
            finally:
                self.mt5 = None

    def _require(self) -> MetaTrader5:
        if self.mt5 is None:
            raise RuntimeError("MT5 client is not connected")
        return self.mt5

    def version(self) -> Any:
        return plain(self._require().version())

    def _remote_eval(self, code: str) -> Any:
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

    def symbol_info(self, symbol: str) -> dict[str, Any] | None:
        return plain(self._remote_eval(f"(lambda x: None if x is None else dict(x._asdict()))(mt5.symbol_info({symbol!r}))"))

    def tick(self, symbol: str) -> dict[str, Any] | None:
        return plain(self._remote_eval(f"(lambda x: None if x is None else dict(x._asdict()))(mt5.symbol_info_tick({symbol!r}))"))

    def bars(self, symbol: str, timeframe: int, count: int = 100) -> list[dict[str, Any]]:
        rates = self._require().copy_rates_from_pos(symbol, timeframe, 0, count)
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
        return plain(self._require().last_error())

    def constants(self) -> dict[str, int]:
        mt5 = self._require()
        return {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
        }
