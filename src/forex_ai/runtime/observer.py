from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from forex_ai.config import RuntimeConfig
from forex_ai.journal.db import initialize, log_event
from forex_ai.journal.deal_audit import audit_mt5_deals
from forex_ai.journal.integration_repository import persist_safety_snapshot
from forex_ai.journal.repository import (
    insert_account,
    insert_market_snapshot,
    insert_position_snapshots,
    upsert_mt5_deals,
    upsert_mt5_orders,
)
from forex_ai.mt5.client import MT5Client
from forex_ai.runtime.ops import ensure_disk_headroom
from forex_ai.runtime.resilience import MT5ResyncCoordinator
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot


@dataclass
class ObserverState:
    stop: bool = False


def _bar_payload(bar: Candle) -> dict:
    return {
        "time": int(bar.time_utc.timestamp()),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "tick_volume": bar.volume,
    }


def _market_payload(base_symbol: str, market: MarketSnapshot, health_state: str) -> dict:
    payload: dict = {
        "kind": "validated_market_snapshot",
        "base_symbol": base_symbol,
        "market_snapshot_fingerprint": market.fingerprint,
        "health_state": health_state,
    }
    for name, timeframe in market.timeframes.items():
        rows = [_bar_payload(bar) for bar in timeframe.closed_bars]
        if timeframe.current_bar is not None:
            rows.append(_bar_payload(timeframe.current_bar))
        payload[name] = rows
    return payload


def run_observer(cfg: RuntimeConfig) -> int:
    """Resilient read-only live journal loop. This module contains no order path."""
    if cfg.mode != "OBSERVE":
        raise RuntimeError(f"Observer requires OBSERVE mode, got {cfg.mode}")

    min_free_bytes = int(os.getenv("FOREX_AI_MIN_FREE_BYTES", str(512 * 1024 * 1024)))
    ensure_disk_headroom(cfg.db_path, min_free_bytes=min_free_bytes)
    initialize(cfg.db_path)
    state = ObserverState()

    def request_stop(signum, _frame):
        state.stop = True
        log_event(cfg.db_path, "INFO", "observer", "stop_requested", {"signal": signum})

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    client = MT5Client(cfg)
    coordinator = MT5ResyncCoordinator(client=client, symbols=cfg.symbols, db_path=cfg.db_path)
    failure_attempt = 0
    log_event(cfg.db_path, "INFO", "observer", "observer_started", {"symbols": cfg.symbols, "resilient": True})

    try:
        while not state.stop:
            ensure_disk_headroom(cfg.db_path, min_free_bytes=min_free_bytes)
            now = datetime.now(timezone.utc)
            outcome = coordinator.sync_once(now_utc=now)
            if not outcome.ready:
                log_event(
                    cfg.db_path,
                    "WARN",
                    "observer",
                    "sync_not_ready",
                    {"health_state": outcome.state.value, "reason": outcome.reason},
                )
                delay = coordinator.backoff.delay(failure_attempt)
                failure_attempt += 1
                time.sleep(max(0.1, delay))
                continue

            failure_attempt = 0
            assert outcome.safety is not None and outcome.broker_state is not None
            persist_safety_snapshot(cfg.db_path, outcome.safety)
            if outcome.raw_account:
                insert_account(cfg.db_path, outcome.raw_account)
            insert_position_snapshots(cfg.db_path, list(outcome.raw_positions))
            upsert_mt5_deals(cfg.db_path, list(outcome.raw_deals))
            audit_mt5_deals(cfg.db_path, list(outcome.raw_deals))
            upsert_mt5_orders(cfg.db_path, list(outcome.raw_orders))

            tick_by_symbol = {tick.symbol: tick for tick in outcome.broker_state.ticks}
            for base, market in outcome.markets.items():
                actual = outcome.symbol_mapping[base]
                tick = tick_by_symbol[actual]
                insert_market_snapshot(
                    cfg.db_path,
                    actual,
                    tick.model_dump(mode="json"),
                    _market_payload(base, market, outcome.state.value),
                )

            log_event(
                cfg.db_path,
                "INFO",
                "observer",
                "sync_healthy",
                {
                    "health_state": outcome.state.value,
                    "mapping": dict(outcome.symbol_mapping),
                    "safety_fingerprint": outcome.safety.fingerprint,
                },
            )
            time.sleep(max(1, cfg.poll_seconds))

        log_event(cfg.db_path, "INFO", "observer", "observer_stopped", {})
        return 0
    finally:
        coordinator.close()
