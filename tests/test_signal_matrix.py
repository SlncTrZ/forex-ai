"""Regression tests for closed-candle read-only indicator matrix."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from forex_ai.dashboard import server
from forex_ai.journal.db import initialize, session
from forex_ai.market.signal_matrix import COLUMNS, ROWS, build_matrix, observe_matrix
from forex_ai.strategy.config import bundled_strategy_snapshot

UTC = timezone.utc
NOW = datetime.now(UTC).replace(second=0, microsecond=0)


def bars(minutes, *, trend=1, current_spike=False):
    count = 270
    first = NOW - timedelta(minutes=minutes * (count - 1))
    records = []
    for i in range(count):
        close = 20000.0 + trend * i * 5.0
        if i == count - 1 and current_spike:
            close = 90000.0
        op = close - trend * 2.0
        records.append({
            "time": int((first + timedelta(minutes=minutes * i)).timestamp()),
            "open": op, "high": max(close, op) + 2,
            "low": min(close, op) - 2, "close": close,
            "tick_volume": 100 + i % 6,
        })
    return records


def _snapshot():
    return bundled_strategy_snapshot()


def test_matrix_8x8_measured_and_strict_symbol_routing():
    btc = build_matrix("BTCUSDm", bars_by_tf={"M5": bars(5), "M15": bars(15)},
                       snapshot=_snapshot(), market_state="ACTIVE", now_utc=NOW)
    assert len(btc["columns"]) >= 8 and len(btc["rows"]) == 8
    assert all(len(row["cells"]) == len(COLUMNS) for row in btc["rows"])
    first = btc["rows"][0]
    assert first["enabled"] is True
    assert first["cells"][0]["indicator"] == "RSI"
    assert first["cells"][0]["state"] == "BULL"
    assert first["cells"][0]["met"] is True
    assert first["cells"][1]["state"] == "BULL"
    assert all(not row["eligible"] and all(c["state"] == "NA" for c in row["cells"])
               for row in btc["rows"][1:])
    assert btc["diagnostic_only"] is True


def test_current_bar_is_excluded_even_if_it_contains_false_breakout():
    clean = build_matrix("BTCUSDm", bars_by_tf={"M5": bars(5), "M15": bars(15)},
                         snapshot=_snapshot(), market_state="ACTIVE", now_utc=NOW)
    tainted = build_matrix("BTCUSDm",
                           bars_by_tf={"M5": bars(5, current_spike=True),
                                       "M15": bars(15, current_spike=True)},
                           snapshot=_snapshot(), market_state="ACTIVE", now_utc=NOW)
    # Forming candle never enters RSI, EMA, breakout or trigger calculations.
    assert clean["rows"] == tainted["rows"]


def test_market_close_old_bars_and_absent_timeframes_have_no_lit_signals():
    for state, bars_by_tf, time in (
        ("OFF_SESSION", {"M5": bars(5), "M15": bars(15)}, NOW),
        ("ACTIVE", {"M5": bars(5), "M15": bars(15)}, NOW + timedelta(hours=1)),
        ("ACTIVE", {}, NOW),
    ):
        matrix = build_matrix("BTCUSDm", bars_by_tf=bars_by_tf,
                              snapshot=_snapshot(), market_state=state, now_utc=time)
        assert all(not cell["met"] for row in matrix["rows"] for cell in row["cells"])
        assert matrix["rows"][0]["cells"][0]["state"] in ("STALE", "UNKNOWN")


def test_dashboard_matrix_only_reads_sanitized_audit_and_fails_stale(tmp_path, monkeypatch):
    db = tmp_path / "signal.db"
    initialize(db)
    monkeypatch.setattr(server, "load_runtime_config", lambda: SimpleNamespace(db_path=db))
    assert server.read_signal_matrix()["matrices"] == []
    matrix = build_matrix("BTCUSDm", bars_by_tf={"M5": bars(5), "M15": bars(15)},
                          snapshot=_snapshot(), market_state="ACTIVE", now_utc=NOW)
    matrix["secret"] = "NEVER_EXPORT_THIS"
    observe_matrix(db, matrix)
    data = server.read_signal_matrix()
    assert data["source"] == "forex_ai_signal_matrix_audit"
    assert len(data["matrices"]) == 1
    assert len(data["matrices"][0]["rows"]) == len(ROWS)
    assert "NEVER_EXPORT_THIS" not in str(data)
    assert data["matrices"][0]["diagnostic_only"] is True
    with session(db) as con:
        con.execute("UPDATE audit_events SET payload_json='{\"policy_version\":\"wrong\"}' "
                    "WHERE event_type='SIGNAL_MATRIX_OBSERVATION'")
    assert server.read_signal_matrix()["matrices"] == []


def test_journal_idempotency_for_same_matrix(tmp_path):
    db = tmp_path / "signal.db"
    initialize(db)
    matrix = build_matrix("BTCUSDm", bars_by_tf={"M5": bars(5), "M15": bars(15)},
                          snapshot=_snapshot(), market_state="ACTIVE", now_utc=NOW)
    observe_matrix(db, matrix)
    observe_matrix(db, matrix)
    with session(db) as con:
        count = con.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='SIGNAL_MATRIX_OBSERVATION'").fetchone()[0]
    assert count == 1
