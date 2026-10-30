"""Isolated MT5 tick-history collector with bounded in-memory SSE fan-out.

One read-only MT5 session serves every browser. No order API is called and no
per-tick SQLite writes occur. Copy-ticks history (not symbol_info_tick) lets us
recover multiple changes between polls; saturation and lag are explicit gaps.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from forex_ai.config import RuntimeConfig, load_runtime_config
from forex_ai.mt5.client import MT5Client

LOG = logging.getLogger(__name__)
MAX_BATCH = 4000
MAX_RING = 12000
POLL_SECONDS = 0.35
UTC = timezone.utc


def read_tick_history(client: MT5Client, symbol: str, start_ms: int, end_ms: int,
                      *, limit: int = MAX_BATCH) -> dict:
    """Query broker tick history; transport only primitive fields over RPyC."""
    if not symbol or limit < 1:
        raise ValueError("invalid symbol/limit")
    start = f"datetime.datetime.fromtimestamp({start_ms / 1000!r}, datetime.timezone.utc)"
    end = f"datetime.datetime.fromtimestamp({end_ms / 1000!r}, datetime.timezone.utc)"
    code = (
        "(lambda rows: {'total':0,'ticks':[]} if rows is None else "
        "{'total':int(len(rows)), 'ticks':[(int(x['time_msc']),float(x['bid']),"
        "float(x['ask']),int(x['flags'])) for x in rows[-" + str(limit) + ":]]})"
        f"(mt5.copy_ticks_range({symbol!r},{start},{end},mt5.COPY_TICKS_ALL))"
    )
    return client._remote_eval(code)


def _resolve_symbols(client: MT5Client, cfg: RuntimeConfig) -> tuple[str, ...]:
    """Prefer actual recent observer-resolved broker symbols, not guessed suffixes."""
    choices = {
        str(row["name"]) for row in client.symbol_candidates(cfg.symbols)
        if row.get("name")
    }
    observed: list[str] = []
    if cfg.db_path.is_file():
        try:
            with sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=2) as db:
                rows = db.execute(
                    "SELECT symbol FROM market_snapshots GROUP BY symbol ORDER BY MAX(id) DESC LIMIT 150"
                ).fetchall()
                observed = [str(row[0]) for row in rows]
        except sqlite3.Error:
            pass
    resolved: list[str] = []
    for base in cfg.symbols:
        matches = [s for s in observed if s in choices and (s.upper() == base.upper() or s.upper().startswith(base.upper()))]
        if matches:
            resolved.append(matches[0])
        elif base in choices:
            resolved.append(base)
        else:
            # Avoid ambiguity between DEMO/REAL suffixes: wait for observer mapping.
            raise RuntimeError("BROKER_SYMBOL_UNRESOLVED:" + base)
    return tuple(dict.fromkeys(resolved))


class TickHub:
    """In-memory cursor and fan-out. Slow consumers must resync on ring overflow."""
    def __init__(self, *, capacity: int = MAX_RING):
        if capacity < 2:
            raise ValueError("capacity must be >= 2")
        self.condition = threading.Condition()
        self.events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.seq = 0
        self.quotes: dict[str, dict] = {}
        self.health = "CONNECTING"
        self.reason = ""
        self.last_poll_ms: int | None = None
        self.last_error_ms: int | None = None
        self.overflows = 0
        self.latest_received_ms: int | None = None
        self.symbols: tuple[str, ...] = ()

    def publish(self, kind: str, payload: dict) -> dict:
        with self.condition:
            self.seq += 1
            event = {"id": self.seq, "type": kind, **payload}
            self.events.append(event)
            if kind == "tick":
                self.quotes[str(payload["symbol"])] = event
                self.latest_received_ms = int(payload["received_ms"])
            elif kind == "gap":
                self.overflows += 1
            self.condition.notify_all()
            return event

    def set_health(self, state: str, reason: str = "") -> None:
        with self.condition:
            changed = state != self.health or reason != self.reason
            self.health, self.reason = state, reason
        if changed:
            self.publish("health", {"state": state, "reason": reason, "received_ms": int(time.time()*1000)})

    def snapshot(self) -> dict:
        with self.condition:
            return {
                "stream_state": self.health, "reason": self.reason,
                "last_poll_ms": self.last_poll_ms, "last_error_ms": self.last_error_ms,
                "last_received_ms": self.latest_received_ms, "latest_id": self.seq,
                "overflows": self.overflows, "symbols": list(self.symbols),
                "quotes": list(self.quotes.values()),
                "buffer_size": len(self.events), "capacity": self.events.maxlen,
                "transport": "MT5_COPY_TICKS_RANGE_SSE",
            }

    def after(self, cursor: int, *, limit: int = 200) -> tuple[list[dict], bool]:
        with self.condition:
            oldest = self.events[0]["id"] if self.events else self.seq + 1
            gap = cursor < oldest - 1
            records = [e for e in self.events if e["id"] > cursor]
            # If consumer requests fewer events than available, return oldest
            # first so it can catch up without silently skipping anything.
            return records[:limit], gap

    def wait_after(self, cursor: int, *, timeout: float = 8) -> tuple[list[dict], bool]:
        with self.condition:
            if self.seq <= cursor:
                self.condition.wait(timeout=timeout)
            return self.after(cursor)


class TickCollector:
    def __init__(self, hub: TickHub, *, config: RuntimeConfig | None = None,
                 client_factory: Callable = MT5Client, poll_seconds: float = POLL_SECONDS):
        self.hub = hub
        self.config = config
        self.client_factory = client_factory
        self.poll_seconds = max(0.2, poll_seconds)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_ms: dict[str, int] = {}
        self.seen: dict[str, Counter] = {}
        self.last_poll_event_ms = 0

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="mt5-tick-collector", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def ingest(self, symbol: str, rows: list, *, received_ms: int, total: int | None = None) -> int:
        """Dedup the inclusive history boundary while retaining equal-MSC changes."""
        count = 0
        cutoff = self.last_ms.get(symbol, received_ms - 2000)
        seen = self.seen.setdefault(symbol, Counter())
        current_ms = cutoff
        current_seen: Counter = Counter()
        if total is not None and total > MAX_BATCH:
            self.hub.publish("gap", {
                "symbol": symbol, "reason": "HISTORY_BATCH_OVERFLOW",
                "missed_lower_bound": total-MAX_BATCH, "received_ms": received_ms,
            })
        for record in sorted(rows, key=lambda x: int(x[0])):
            ms, bid, ask, flags = int(record[0]), float(record[1]), float(record[2]), int(record[3])
            if ms <= 0 or ms > received_ms + 60_000:
                continue
            fingerprint = (bid, ask, flags)
            if ms < cutoff:
                continue
            if ms == cutoff:
                current_seen[fingerprint] += 1
                if current_seen[fingerprint] <= seen[fingerprint]:
                    continue
            else:
                if ms != current_ms:
                    current_seen = Counter()
                    current_ms = ms
                current_seen[fingerprint] += 1
            if ms > cutoff:
                self.last_ms[symbol] = ms
                self.seen[symbol] = Counter()
                cutoff = ms
                seen = self.seen[symbol]
            seen[fingerprint] += 1
            self.hub.publish("tick", {
                "symbol": symbol, "broker_time_ms": ms, "received_ms": received_ms,
                "bid": bid, "ask": ask, "spread": round(ask-bid, 8),
                "flags": flags, "age_at_receive_ms": max(0, received_ms-ms),
            })
            count += 1
        return count

    def run(self) -> None:
        retry = 0
        while not self.stop_event.is_set():
            client = None
            try:
                cfg = self.config or load_runtime_config()
                client = self.client_factory(cfg)
                if not client.connect():
                    raise RuntimeError("MT5_BRIDGE_CONNECT_FAILED")
                symbols = _resolve_symbols(client, cfg)
                with self.hub.condition:
                    self.hub.symbols = symbols
                self.hub.set_health("CONNECTED")
                retry = 0
                while not self.stop_event.is_set():
                    began = time.monotonic()
                    received_ms = int(time.time() * 1000)
                    for symbol in symbols:
                        if self.stop_event.is_set():
                            break
                        start = self.last_ms.get(symbol, received_ms-2000)
                        # Include same-millisecond ticks and one second of history
                        # for broker UTC datetime rounding; dedup by time+values.
                        history = read_tick_history(
                            client, symbol, max(0, start-1000), received_ms+1000,
                        )
                        self.ingest(symbol, history["ticks"],
                                    received_ms=int(time.time()*1000), total=history["total"])
                    poll_ms = int(time.time()*1000)
                    with self.hub.condition:
                        self.hub.last_poll_ms = poll_ms
                    if poll_ms-self.last_poll_event_ms >= 2000:
                        self.last_poll_event_ms = poll_ms
                        self.hub.publish("poll", {"last_poll_ms": poll_ms})
                    self.stop_event.wait(max(0.02, self.poll_seconds-(time.monotonic()-began)))
            except Exception as exc:
                LOG.warning("tick collector disconnected: %s", type(exc).__name__)
                with self.hub.condition:
                    self.hub.last_error_ms = int(time.time()*1000)
                self.hub.set_health("RECONNECTING", "BROKER_TICK_STREAM_UNAVAILABLE")
                retry = min(retry+1, 6)
                self.stop_event.wait(min(6, 0.5 * 2**(retry-1)))
            finally:
                if client:
                    client.close()
        self.hub.set_health("STOPPED")
