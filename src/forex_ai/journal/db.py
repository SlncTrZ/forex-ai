from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 5

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    login INTEGER,
    server TEXT,
    currency TEXT,
    balance REAL,
    equity REAL,
    margin REAL,
    free_margin REAL,
    margin_level REAL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bid REAL,
    ask REAL,
    spread REAL,
    timeframe TEXT,
    market_regime TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_symbol_time ON market_snapshots(symbol, timestamp);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    score REAL,
    proposed_entry REAL,
    proposed_sl REAL,
    proposed_tp REAL,
    rr REAL,
    feature_snapshot_id INTEGER,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_keys (
    signal_key TEXT PRIMARY KEY,
    signal_id INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_runs (
    run_id TEXT PRIMARY KEY,
    signal_id INTEGER,
    correlation_id TEXT,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    started_epoch_ms INTEGER NOT NULL,
    completed_at_utc TEXT,
    completed_epoch_ms INTEGER,
    status TEXT NOT NULL,
    initial_context_json TEXT NOT NULL,
    tool_trace_json TEXT NOT NULL DEFAULT '[]',
    raw_response_text TEXT,
    error_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_runs_signal ON llm_runs(signal_id, started_epoch_ms);
CREATE INDEX IF NOT EXISTS idx_llm_runs_correlation ON llm_runs(correlation_id, started_epoch_ms);

CREATE TABLE IF NOT EXISTS llm_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    signal_id INTEGER,
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    action TEXT NOT NULL,
    confidence REAL,
    thesis TEXT,
    risks_json TEXT NOT NULL DEFAULT '[]',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    api_cost_usd REAL DEFAULT 0,
    latency_ms INTEGER,
    raw_response_hash TEXT
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source_decision_id INTEGER,
    approved INTEGER NOT NULL,
    reason_codes_json TEXT NOT NULL,
    requested_lot REAL,
    approved_lot REAL,
    calculated_risk REAL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    mt5_order_ticket INTEGER,
    mt5_deal_ticket INTEGER,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    volume REAL NOT NULL,
    requested_price REAL,
    executed_price REAL,
    sl REAL,
    tp REAL,
    deviation REAL,
    retcode INTEGER,
    execution_payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    source_mode TEXT,
    open_time TEXT,
    close_time TEXT,
    entry_price REAL,
    exit_price REAL,
    volume REAL,
    sl REAL,
    tp REAL,
    gross_pnl REAL,
    commission REAL,
    swap REAL,
    net_pnl REAL,
    mfe REAL,
    mae REAL,
    exit_reason TEXT,
    mt5_position_id INTEGER UNIQUE,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trade_id INTEGER,
    symbol TEXT,
    setup TEXT,
    regime TEXT,
    lesson_type TEXT NOT NULL,
    lesson_text TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    confidence REAL,
    active INTEGER NOT NULL DEFAULT 1,
    superseded_by INTEGER
);

CREATE TABLE IF NOT EXISTS shadow_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source_snapshot_id INTEGER,
    actor TEXT NOT NULL,
    hypothetical_action TEXT NOT NULL,
    hypothetical_entry REAL,
    hypothetical_sl REAL,
    hypothetical_tp REAL,
    horizon TEXT,
    hypothetical_pnl REAL,
    result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS mt5_deals (
    ticket INTEGER PRIMARY KEY,
    order_ticket INTEGER,
    time INTEGER,
    time_msc INTEGER,
    type INTEGER,
    entry INTEGER,
    magic INTEGER,
    position_id INTEGER,
    symbol TEXT,
    volume REAL,
    price REAL,
    commission REAL,
    swap REAL,
    profit REAL,
    fee REAL,
    reason INTEGER,
    comment TEXT,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mt5_deals_time ON mt5_deals(time);
CREATE INDEX IF NOT EXISTS idx_mt5_deals_position ON mt5_deals(position_id);

CREATE TABLE IF NOT EXISTS mt5_orders_history (
    ticket INTEGER PRIMARY KEY,
    time_setup INTEGER,
    time_done INTEGER,
    type INTEGER,
    state INTEGER,
    magic INTEGER,
    position_id INTEGER,
    position_by_id INTEGER,
    symbol TEXT,
    volume_initial REAL,
    volume_current REAL,
    price_open REAL,
    sl REAL,
    tp REAL,
    price_current REAL,
    reason INTEGER,
    comment TEXT,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mt5_orders_time ON mt5_orders_history(time_setup);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ticket INTEGER NOT NULL,
    identifier INTEGER,
    symbol TEXT NOT NULL,
    type INTEGER,
    volume REAL,
    price_open REAL,
    sl REAL,
    tp REAL,
    price_current REAL,
    swap REAL,
    profit REAL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_snapshots_ticket_time ON position_snapshots(ticket, timestamp);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    epoch_ms INTEGER NOT NULL,
    correlation_id TEXT,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT,
    entity_id TEXT,
    market_time_msc INTEGER,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(epoch_ms);
CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_events(correlation_id, epoch_ms);
CREATE INDEX IF NOT EXISTS idx_audit_symbol ON audit_events(symbol, epoch_ms);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    severity TEXT NOT NULL,
    component TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON system_events(timestamp);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def utc_now_parts() -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    return now.isoformat(timespec="microseconds"), int(now.timestamp() * 1000)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def initialize(path: Path) -> None:
    with connect(path) as con:
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO schema_meta(key,value) VALUES('schema_version',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        con.commit()


@contextmanager
def session(path: Path) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def log_event(path: Path, severity: str, component: str, event_type: str, payload: dict) -> None:
    with session(path) as con:
        con.execute(
            "INSERT INTO system_events(timestamp,severity,component,event_type,payload_json) VALUES(?,?,?,?,?)",
            (utc_now(), severity, component, event_type, json.dumps(payload, ensure_ascii=False, default=str)),
        )


def log_audit_event(
    path: Path,
    *,
    event_type: str,
    source: str,
    payload: dict,
    correlation_id: str | None = None,
    symbol: str | None = None,
    entity_id: str | None = None,
    market_time_msc: int | None = None,
) -> int:
    timestamp_utc, epoch_ms = utc_now_parts()
    with session(path) as con:
        cur = con.execute(
            """INSERT INTO audit_events(
                timestamp_utc,epoch_ms,correlation_id,event_type,source,symbol,entity_id,market_time_msc,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                timestamp_utc, epoch_ms, correlation_id, event_type, source, symbol,
                entity_id, market_time_msc, json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        return int(cur.lastrowid)
