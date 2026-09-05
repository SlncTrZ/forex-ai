from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from forex_ai.journal.db import initialize, session
from forex_ai.kernel.health import HealthState
from forex_ai.market.context_config import load_market_context_snapshot
from forex_ai.runtime.resilience import MT5ResyncCoordinator, _expected_daily_rollover_gap

UTC = timezone.utc
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)


class FakeMT5:
    def __init__(self):
        self.connect_results = [True]
        self.login = 123
        self.ambiguous = False
        self.stale_tick = False
        self.gap_bars = False
        self.unprotected = False
        self.closed = 0
        self.close_raises = False
        self.symbol_calls = 0
        self.bar_calls = 0
        self.history_order_calls = 0
        self.history_deal_calls = 0

    def connect(self):
        return self.connect_results.pop(0) if self.connect_results else True

    def close(self):
        self.closed += 1
        if self.close_raises:
            raise EOFError("stream has been closed")

    def account_info(self):
        return {
            "login": self.login, "server": "Broker-Demo", "currency": "USD", "balance": 1000.0,
            "equity": 1000.0, "margin": 0.0, "margin_free": 1000.0, "leverage": 100,
        }

    def symbols(self):
        self.symbol_calls += 1
        rows = [{"name": "EURUSD.a"}]
        if self.ambiguous:
            rows.append({"name": "EURUSD.b"})
        return rows

    def symbol_info(self, symbol):
        assert symbol == "EURUSD.a"
        return {
            "name": symbol, "digits": 5, "point": 0.00001, "trade_contract_size": 100000,
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
            "trade_stops_level": 10, "trade_freeze_level": 0,
            "trade_mode": 1, "order_mode": 1, "filling_mode": 1,
            "currency_base": "EUR", "currency_profit": "USD", "currency_margin": "EUR",
        }

    def tick(self, symbol):
        seconds = 60 if self.stale_tick else 0
        when = NOW - timedelta(seconds=seconds)
        return {"bid": 1.0999, "ask": 1.1000, "time": int(when.timestamp()), "time_msc": int(when.timestamp() * 1000)}

    def bars(self, symbol, timeframe, count=100):
        self.bar_calls += 1
        seconds = {5: 300, 15: 900, 60: 3600, 240: 14400}[timeframe]
        start = NOW - timedelta(seconds=seconds * 60)
        rows = []
        for i in range(60):
            index = i + (2 if self.gap_bars and i >= 30 else 0)
            ts = start + timedelta(seconds=seconds * index)
            rows.append({"time": int(ts.timestamp()), "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.1, "tick_volume": 100})
        return rows

    def positions(self):
        if not self.unprotected:
            return []
        return [{
            "ticket": 10, "symbol": "EURUSD.a", "type": 0, "volume": 0.01,
            "price_open": 1.1, "price_current": 1.1, "sl": 0.0, "tp": 1.12,
            "profit": 0.0, "magic": 7, "comment": "test",
        }]

    def active_orders(self):
        return []

    def history_orders(self, start_ts, end_ts):
        self.history_order_calls += 1
        return []

    def history_deals(self, start_ts, end_ts):
        self.history_deal_calls += 1
        return [{"ticket": 99, "order": 0, "position_id": 0, "symbol": "", "volume": 0.0, "price": 0.0, "profit": 100.0, "time_msc": int(NOW.timestamp()*1000)}]

    def constants(self):
        return {
            "M5": 5, "M15": 15, "H1": 60, "H4": 240,
            "POSITION_TYPE_BUY": 0, "POSITION_TYPE_SELL": 1,
            "SYMBOL_TRADE_MODE_DISABLED": 0, "SYMBOL_ORDER_MARKET": 1,
        }


def coordinator(tmp_path, fake):
    db = tmp_path / "runtime.db"
    initialize(db)
    return MT5ResyncCoordinator(client=fake, symbols=("EURUSD",), db_path=db, bars_count=60, clock=lambda: NOW), db


def test_startup_connection_failure_is_fail_closed_and_heartbeat_persisted(tmp_path):
    fake = FakeMT5(); fake.connect_results = [False]
    coord, db = coordinator(tmp_path, fake)
    out = coord.sync_once(now_utc=NOW)
    assert out.state is HealthState.DISCONNECTED and not out.ready
    with session(db) as con:
        states = [row[0] for row in con.execute("select health_state from runtime_heartbeats order by id")]
    assert states == ["CONNECTING", "DISCONNECTED"]


def test_full_sync_is_healthy_and_can_repeat_from_healthy_state(tmp_path):
    fake = FakeMT5(); coord, db = coordinator(tmp_path, fake)
    first = coord.sync_once(now_utc=NOW)
    second = coord.sync_once(now_utc=NOW + timedelta(seconds=1))
    assert first.ready and second.ready
    assert first.symbol_mapping == {"EURUSD": "EURUSD.a"}
    assert len(first.markets["EURUSD"].timeframes["M15"].closed_bars) == 59
    assert fake.symbol_calls == 1
    assert fake.bar_calls == 4
    assert fake.history_order_calls == 1
    assert fake.history_deal_calls == 1
    with session(db) as con:
        assert con.execute("select count(*) from runtime_heartbeats").fetchone()[0] >= 5


def test_cleanup_failure_does_not_escape_resync_loop(tmp_path):
    fake = FakeMT5(); fake.stale_tick = True; fake.close_raises = True
    coord, _ = coordinator(tmp_path, fake)
    out = coord.sync_once(now_utc=NOW)
    assert out.state is HealthState.DEGRADED
    assert not out.ready
    assert not coord.connected
    assert fake.closed == 1


def test_stale_tick_degrades_and_forces_reconnect(tmp_path):
    fake = FakeMT5(); fake.stale_tick = True
    coord, _ = coordinator(tmp_path, fake)
    out = coord.sync_once(now_utc=NOW)
    assert out.state is HealthState.DEGRADED and not out.ready
    assert "STALE_TICK" in out.reason
    assert not coord.connected and fake.closed == 1


def test_ambiguous_symbol_mapping_fails_closed(tmp_path):
    fake = FakeMT5(); fake.ambiguous = True
    coord, _ = coordinator(tmp_path, fake)
    out = coord.sync_once(now_utc=NOW)
    assert out.state is HealthState.DEGRADED
    assert "SYMBOL_MAPPING_UNRESOLVED" in out.reason


def test_daily_rollover_gap_classifier_is_narrow():
    left = datetime(2026, 9, 3, 20, 45, tzinfo=UTC)
    assert _expected_daily_rollover_gap(left, datetime(2026, 9, 3, 22, 0, tzinfo=UTC))
    assert not _expected_daily_rollover_gap(
        datetime(2026, 9, 3, 12, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 13, 15, tzinfo=UTC),
    )
    assert not _expected_daily_rollover_gap(left, datetime(2026, 9, 3, 23, 15, tzinfo=UTC))


def test_gap_in_closed_bars_fails_closed(tmp_path):
    fake = FakeMT5(); fake.gap_bars = True
    coord, _ = coordinator(tmp_path, fake)
    out = coord.sync_once(now_utc=NOW)
    assert out.state is HealthState.DEGRADED
    assert "GAPPED_BARS" in out.reason


def test_account_identity_drift_blocks_after_reconciliation(tmp_path):
    fake = FakeMT5(); coord, _ = coordinator(tmp_path, fake)
    assert coord.sync_once(now_utc=NOW).ready
    fake.login = 999
    out = coord.sync_once(now_utc=NOW + timedelta(seconds=1))
    assert out.state is HealthState.BLOCKED and not out.ready
    assert out.safety is not None and "ACCOUNT_IDENTITY_DRIFT" in out.safety.blocking_reasons


def test_unprotected_position_blocks_sync_safety(tmp_path):
    fake = FakeMT5(); fake.unprotected = True
    coord, _ = coordinator(tmp_path, fake)
    out = coord.sync_once(now_utc=NOW)
    assert out.state is HealthState.BLOCKED and not out.ready
    assert out.safety is not None and "UNPROTECTED_POSITION" in out.safety.blocking_reasons


class ContextFailureMT5(FakeMT5):
    def constants(self):
        values = super().constants()
        values["D1"] = 1440
        return values

    def bars_universe_bundle(self, symbols, timeframes, count):
        raise RuntimeError("context feed unavailable")


def test_higher_timeframe_context_failure_never_blocks_core_sync(tmp_path):
    fake = ContextFailureMT5()
    db = tmp_path / "runtime-context.db"
    initialize(db)
    context = load_market_context_snapshot(Path("config/market-context.yaml"), allow_last_good=False)
    coord = MT5ResyncCoordinator(
        client=fake,
        symbols=("EURUSD",),
        db_path=db,
        bars_count=60,
        clock=lambda: NOW,
        market_context=context,
    )
    out = coord.sync_once(now_utc=NOW)
    assert out.ready
    payload = out.markets["EURUSD"].context["higher_timeframe_structure"]
    assert payload["status"] == "UNAVAILABLE"
    assert "context feed unavailable" in payload["error"]
