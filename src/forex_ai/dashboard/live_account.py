"""Read-only live MT5 financial telemetry, independent of stale SQLite snapshots.

Runs in one daemon worker, never calls trade/order methods. Every value is
timestamped and stale data is deliberately hidden rather than shown as live.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import datetime, timezone
from typing import Any

from forex_ai.config import load_runtime_config
from forex_ai.execution.account_mode import detect_account_trade_mode
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import account_matches

UTC = timezone.utc
MAX_AGE_SECONDS = 25


def _amount(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        x = float(value)
        return round(x, 2) if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def broker_financial_snapshot(client: MT5Client, now: datetime | None = None) -> dict:
    """Authoritative MT5 account and symbol-trade deal PnL, no inferred deposits."""
    now = now or datetime.now(UTC)
    info = client.account_info()
    if not isinstance(info, dict) or not account_matches(info):
        raise RuntimeError("BROKER_ACCOUNT_UNBOUND")
    mode = detect_account_trade_mode(info).name
    if mode not in ("DEMO", "REAL"):
        raise RuntimeError("BROKER_ACCOUNT_MODE_UNSUPPORTED")
    balance, equity = _amount(info.get("balance")), _amount(info.get("equity"))
    if balance is None or equity is None:
        raise RuntimeError("BROKER_FINANCIALS_INVALID")
    # Client history_deals uses broker-reported timestamps and identifies
    # actual trade PnL by a nonempty symbol. Balance/credit/deposit events
    # have no symbol and are excluded from trading PnL.
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    deals = client.history_deals(today.timestamp(), now.timestamp())
    if deals is None:
        raise RuntimeError("BROKER_DEALS_UNAVAILABLE")
    trade_deals = [d for d in deals if d.get("symbol")]
    realized = sum(
        sum((_amount(d.get(k)) or 0) for k in ("profit", "commission", "swap", "fee"))
        for d in trade_deals
    )
    positions = client.positions()
    if positions is None:
        raise RuntimeError("BROKER_POSITIONS_UNAVAILABLE")
    floating = _amount(info.get("profit"))
    if floating is None:
        floating = sum((_amount(p.get("profit")) or 0) for p in positions)
    return {
        "source": "MT5_DIRECT", "mode": mode, "bound": True,
        "observed_at": now.isoformat(), "balance": balance, "equity": equity,
        "floating_pnl": round(floating, 2), "realized_pnl_utc_day": round(realized, 2),
        "margin": _amount(info.get("margin")),
        "free_margin": _amount(info.get("margin_free")),
        "currency": str(info.get("currency") or ""),
        "positions": len(positions), "trade_deals_utc_day": len(trade_deals),
    }


class LiveAccountCache:
    def __init__(self, *, poll_seconds: float = 8):
        self.poll_seconds = max(3.0, poll_seconds)
        self.lock = threading.Lock()
        self.latest: dict | None = None
        self.last_error: str | None = None
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.stop_event.clear()
        self.worker = threading.Thread(target=self.run, name="read-only-mt5-financials", daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=3)

    def run(self) -> None:
        while not self.stop_event.is_set():
            client = None
            try:
                client = MT5Client(load_runtime_config())
                if not client.connect():
                    raise RuntimeError("BROKER_CONNECT_FAILED")
                data = broker_financial_snapshot(client)
                with self.lock:
                    self.latest = data
                    self.last_error = None
            except Exception as exc:
                with self.lock:
                    self.last_error = type(exc).__name__
            finally:
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
            self.stop_event.wait(self.poll_seconds)

    def snapshot(self) -> dict:
        now = datetime.now(UTC)
        with self.lock:
            current = dict(self.latest) if self.latest else None
            error = self.last_error
        if current is None:
            return {"state": "UNAVAILABLE", "age_seconds": None,
                    "source": "MT5_DIRECT", "reason": error or "WAITING_FOR_BROKER"}
        try:
            age = max(0, (now-datetime.fromisoformat(current["observed_at"])).total_seconds())
        except (ValueError, TypeError):
            age = float("inf")
        if age > MAX_AGE_SECONDS:
            return {"state": "STALE", "age_seconds": round(age, 1),
                    "source": "MT5_DIRECT", "reason": error or "BROKER_SNAPSHOT_EXPIRED"}
        return {**current, "state": "LIVE", "age_seconds": round(age, 1)}
