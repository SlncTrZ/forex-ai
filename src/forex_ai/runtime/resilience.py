from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from forex_ai.integration.adapters import (
    account_snapshot,
    broker_position,
    market_snapshot,
    symbol_contract,
    tick_snapshot,
    timeframe_snapshot,
)
from forex_ai.journal.runtime_health import RuntimeHeartbeat, persist_heartbeat
from forex_ai.kernel.health import BackoffPolicy, HealthKernel, HealthState
from forex_ai.mt5.contracts import BrokerDeal, BrokerOrder, BrokerState, SafetySnapshot
from forex_ai.mt5.symbols import resolve_symbol_strict
from forex_ai.strategy.v1.contracts import MarketSnapshot


class MT5RuntimePort(Protocol):
    def connect(self) -> bool: ...
    def close(self) -> None: ...
    def account_info(self) -> dict[str, Any] | None: ...
    def symbols(self) -> list[dict[str, Any]]: ...
    def symbol_info(self, symbol: str) -> dict[str, Any] | None: ...
    def tick(self, symbol: str) -> dict[str, Any] | None: ...
    def bars(self, symbol: str, timeframe: int, count: int = 100) -> list[dict[str, Any]]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def active_orders(self) -> list[dict[str, Any]]: ...
    def history_orders(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]: ...
    def history_deals(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]: ...
    def constants(self) -> dict[str, int]: ...


@dataclass(frozen=True)
class SyncOutcome:
    state: HealthState
    safety: SafetySnapshot | None
    broker_state: BrokerState | None
    markets: Mapping[str, MarketSnapshot]
    symbol_mapping: Mapping[str, str]
    raw_account: dict[str, Any] | None = None
    raw_positions: tuple[dict[str, Any], ...] = ()
    raw_orders: tuple[dict[str, Any], ...] = ()
    raw_deals: tuple[dict[str, Any], ...] = ()
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.state is HealthState.HEALTHY and self.safety is not None and self.safety.reconciled


class SyncError(RuntimeError):
    pass


def _order(raw: Mapping[str, Any]) -> BrokerOrder:
    return BrokerOrder(
        ticket=int(raw["ticket"]), symbol=str(raw["symbol"]),
        volume_initial=float(raw.get("volume_initial") or 0.0), volume_current=float(raw.get("volume_current") or 0.0),
        price_open=float(raw.get("price_open") or 0.0), sl=float(raw.get("sl") or 0.0), tp=float(raw.get("tp") or 0.0),
        state=int(raw["state"]) if raw.get("state") is not None else None,
        magic=int(raw["magic"]) if raw.get("magic") is not None else None,
        comment=str(raw.get("comment") or ""),
    )


def _is_trade_deal(raw: Mapping[str, Any]) -> bool:
    return bool(raw.get("symbol")) and float(raw.get("volume") or 0.0) > 0 and float(raw.get("price") or 0.0) > 0


def _deal(raw: Mapping[str, Any]) -> BrokerDeal:
    return BrokerDeal(
        ticket=int(raw["ticket"]), order=int(raw.get("order") or 0), position_id=int(raw.get("position_id") or 0),
        symbol=str(raw["symbol"]), volume=float(raw["volume"]), price=float(raw["price"]),
        profit=float(raw.get("profit") or 0.0), time_msc=int(raw["time_msc"]),
    )


def _expected_weekend_gap(left: datetime, right: datetime) -> bool:
    if left.weekday() == 4 and right.weekday() in {6, 0}:
        return True
    return (right - left) >= timedelta(hours=36) and left.weekday() in {4, 5} and right.weekday() in {0, 6}


def validate_bar_gaps(market: MarketSnapshot, timeframe_seconds: Mapping[str, int]) -> None:
    for name, tf in market.timeframes.items():
        seconds = timeframe_seconds.get(name)
        if not seconds:
            continue
        gaps: list[tuple[object, object, tuple[int, int, int, int, int]]] = []
        for left, right in zip(tf.closed_bars[:-1], tf.closed_bars[1:]):
            delta = (right.time_utc - left.time_utc).total_seconds()
            if delta <= seconds * 1.5 or _expected_weekend_gap(left.time_utc, right.time_utc):
                continue
            multiples = round(delta / seconds)
            signature = (left.time_utc.hour, left.time_utc.minute, right.time_utc.hour, right.time_utc.minute, multiples)
            gaps.append((left, right, signature))
        counts = Counter(signature for _, _, signature in gaps)
        for left, right, signature in gaps:
            # MT5 Python does not expose per-symbol trading sessions. A repeated
            # short gap at the same UTC hours across the sampled history is treated
            # as a broker session break; isolated gaps remain safety failures.
            if counts[signature] >= 2 and signature[-1] <= 8:
                continue
            raise SyncError(
                f"GAPPED_BARS:{market.symbol}:{name}:{left.time_utc.isoformat()}->{right.time_utc.isoformat()}"
            )


class MT5ResyncCoordinator:
    def __init__(
        self,
        *,
        client: MT5RuntimePort,
        symbols: tuple[str, ...],
        db_path: Path,
        max_tick_age_seconds: int = 5,
        bars_count: int = 200,
        history_lookback_seconds: int = 2 * 86400,
        bars_refresh_seconds: int = 60,
        history_refresh_seconds: int = 60,
        backoff: BackoffPolicy | None = None,
        health: HealthKernel | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.client = client
        self.symbols = symbols
        self.db_path = db_path
        self.max_tick_age_seconds = max_tick_age_seconds
        self.bars_count = bars_count
        self.history_lookback_seconds = history_lookback_seconds
        self.bars_refresh_seconds = bars_refresh_seconds
        self.history_refresh_seconds = history_refresh_seconds
        self.backoff = backoff or BackoffPolicy()
        self.health = health or HealthKernel()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.connected = False
        self.last_mt5_success_utc: datetime | None = None
        self.last_market_time_msc: int | None = None
        self.last_journal_success_utc: datetime | None = None
        self._mapping: dict[str, str] = {}
        self._timeframes: dict[str, dict[str, Any]] = {}
        self._last_bars_refresh_utc: datetime | None = None
        self._last_history_refresh_utc: datetime | None = None
        self._raw_orders_cache: tuple[dict[str, Any], ...] = ()
        self._raw_deals_cache: tuple[dict[str, Any], ...] = ()
        self._recent_orders_cache: tuple[BrokerOrder, ...] = ()
        self._recent_deals_cache: tuple[BrokerDeal, ...] = ()

    def sync_once(self, *, now_utc: datetime) -> SyncOutcome:
        now = now_utc.astimezone(timezone.utc)
        try:
            if not self.connected:
                self.health.begin_connect()
                self._heartbeat(now, "CONNECTING")
                if not self.client.connect():
                    self.health.connection_failed()
                    self._heartbeat(now, "CONNECTION_FAILED")
                    return SyncOutcome(self.health.state, None, None, {}, {}, reason="CONNECTION_FAILED")
                self.connected = True

            self.health.begin_sync()
            self._heartbeat(now, "SYNCING")
            outcome = self._resync(now)
            self.last_mt5_success_utc = now
            self.last_market_time_msc = max((tick.time_msc for tick in outcome.broker_state.ticks), default=None) if outcome.broker_state else None
            self._heartbeat(now, "SYNC_COMPLETE", payload={"symbols": dict(outcome.symbol_mapping)})
            return outcome
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            self.health.degrade(reason)
            self._heartbeat(now, reason)
            try:
                self.client.close()
            except Exception:
                pass
            finally:
                self.connected = False
                self._invalidate_caches()
            return SyncOutcome(self.health.state, None, None, {}, {}, reason=reason)

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass
        self.connected = False
        self._invalidate_caches()
        self.health.connection_failed()

    def _invalidate_caches(self) -> None:
        self._mapping.clear()
        self._timeframes.clear()
        self._last_bars_refresh_utc = None
        self._last_history_refresh_utc = None
        self._raw_orders_cache = ()
        self._raw_deals_cache = ()
        self._recent_orders_cache = ()
        self._recent_deals_cache = ()

    def _resync(self, now: datetime) -> SyncOutcome:
        raw_account = self.client.account_info()
        if not raw_account:
            raise SyncError("ACCOUNT_UNAVAILABLE")
        account = account_snapshot(raw_account, captured_at_utc=now)

        if not self._mapping:
            available = self.client.symbols()
            if not available:
                raise SyncError("SYMBOL_LIST_UNAVAILABLE")
            mapping: dict[str, str] = {}
            for base in self.symbols:
                resolved = resolve_symbol_strict(base, available)
                if resolved is None:
                    raise SyncError(f"SYMBOL_MAPPING_UNRESOLVED:{base}")
                mapping[base] = resolved
            self._mapping = mapping
        mapping = dict(self._mapping)

        constants = self.client.constants()
        required_constants = {
            "M15", "H1", "H4", "POSITION_TYPE_BUY", "POSITION_TYPE_SELL",
            "SYMBOL_TRADE_MODE_DISABLED", "SYMBOL_ORDER_MARKET",
        }
        missing_constants = sorted(required_constants - constants.keys())
        if missing_constants:
            raise SyncError(f"MISSING_MT5_CONSTANTS:{','.join(missing_constants)}")

        timeframe_seconds = {"M15": 900, "H1": 3600, "H4": 14400}
        refresh_bars = (
            not self._timeframes
            or self._last_bars_refresh_utc is None
            or (now - self._last_bars_refresh_utc).total_seconds() >= self.bars_refresh_seconds
        )
        contracts = []
        ticks = []
        markets: dict[str, MarketSnapshot] = {}
        for base, actual in mapping.items():
            info = self.client.symbol_info(actual)
            if not info:
                raise SyncError(f"SYMBOL_INFO_UNAVAILABLE:{actual}")
            raw_tick = self.client.tick(actual)
            if not raw_tick:
                raise SyncError(f"TICK_UNAVAILABLE:{actual}")
            tick = tick_snapshot(raw_tick, symbol=actual, captured_at_utc=now)
            tick_reference_utc = self.clock().astimezone(timezone.utc)
            tick_age = (tick_reference_utc.timestamp() * 1000 - tick.time_msc) / 1000.0
            if tick_age < -2:
                raise SyncError(f"CLOCK_DRIFT_FUTURE_TICK:{actual}")
            if tick_age > self.max_tick_age_seconds:
                raise SyncError(f"STALE_TICK:{actual}")

            trade_mode = int(info.get("trade_mode") or 0)
            order_mode = int(info.get("order_mode") or 0)
            trade_allowed = trade_mode != int(constants["SYMBOL_TRADE_MODE_DISABLED"])
            market_orders_allowed = bool(order_mode & int(constants["SYMBOL_ORDER_MARKET"]))
            contract = symbol_contract(
                info, symbol=actual, trade_allowed=trade_allowed,
                market_orders_allowed=market_orders_allowed, session_open=trade_allowed and market_orders_allowed,
            )
            if not trade_allowed:
                raise SyncError(f"SYMBOL_NOT_TRADEABLE:{actual}")
            if not market_orders_allowed:
                raise SyncError(f"MARKET_ORDERS_NOT_ALLOWED:{actual}")

            if refresh_bars or base not in self._timeframes:
                timeframes = {}
                for name in ("H4", "H1", "M15"):
                    rows = self.client.bars(actual, constants[name], self.bars_count)
                    if len(rows) < 51:
                        raise SyncError(f"INSUFFICIENT_BARS:{actual}:{name}")
                    timeframes[name] = timeframe_snapshot(name, rows)
                self._timeframes[base] = timeframes
            timeframes = self._timeframes[base]
            market = market_snapshot(symbol=actual, tick=tick, captured_at_utc=now, timeframes=timeframes)
            validate_bar_gaps(market, timeframe_seconds)
            contracts.append(contract)
            ticks.append(tick)
            markets[base] = market
        if refresh_bars:
            self._last_bars_refresh_utc = now

        raw_positions = tuple(self.client.positions())
        positions = tuple(
            broker_position(raw, buy_type=constants["POSITION_TYPE_BUY"], sell_type=constants["POSITION_TYPE_SELL"])
            for raw in raw_positions
        )
        raw_active_orders = tuple(self.client.active_orders())
        pending_orders = tuple(_order(raw) for raw in raw_active_orders)
        refresh_history = (
            self._last_history_refresh_utc is None
            or (now - self._last_history_refresh_utc).total_seconds() >= self.history_refresh_seconds
        )
        if refresh_history:
            start_ts = now.timestamp() - self.history_lookback_seconds
            self._raw_orders_cache = tuple(self.client.history_orders(start_ts, now.timestamp()))
            self._raw_deals_cache = tuple(self.client.history_deals(start_ts, now.timestamp()))
            self._recent_orders_cache = tuple(_order(raw) for raw in self._raw_orders_cache)
            self._recent_deals_cache = tuple(_deal(raw) for raw in self._raw_deals_cache if _is_trade_deal(raw))
            self._last_history_refresh_utc = now
        raw_orders = self._raw_orders_cache
        raw_deals = self._raw_deals_cache
        recent_orders = self._recent_orders_cache
        recent_deals = self._recent_deals_cache

        broker_state = BrokerState(
            account=account, contracts=tuple(contracts), ticks=tuple(ticks), positions=positions,
            pending_orders=pending_orders, recent_orders=recent_orders, recent_deals=recent_deals,
            reconciled_at_utc=now,
        )
        safety = self.health.complete_sync(broker_state)
        return SyncOutcome(
            self.health.state, safety, broker_state, markets, mapping,
            raw_account=dict(raw_account), raw_positions=raw_positions, raw_orders=raw_orders, raw_deals=raw_deals,
            reason="HEALTHY" if safety.reconciled else ",".join(safety.blocking_reasons),
        )

    def _heartbeat(self, now: datetime, reason: str, *, payload: dict[str, Any] | None = None) -> None:
        heartbeat = RuntimeHeartbeat(
            timestamp_utc=now, health_state=self.health.state, reason=reason,
            last_mt5_success_utc=self.last_mt5_success_utc, last_market_time_msc=self.last_market_time_msc,
            last_journal_success_utc=self.last_journal_success_utc, payload=payload,
        )
        persist_heartbeat(self.db_path, heartbeat)
        self.last_journal_success_utc = now
