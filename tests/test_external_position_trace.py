from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from forex_ai.execution.mt5 import intent_comment
from forex_ai.execution.state import ExecutionState, OrderIntent
from forex_ai.journal.db import initialize, session
from forex_ai.journal.external_positions import trace_external_positions
from forex_ai.journal.integration_repository import SQLiteIntentRepository

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def raw_position(*, ticket=1001, sl=0.0, tp=0.0, volume=0.05, comment=""):
    return {
        "ticket": ticket,
        "identifier": ticket,
        "symbol": "XAUUSDc",
        "type": 0,
        "volume": volume,
        "price_open": 4400.0,
        "sl": sl,
        "tp": tp,
        "price_current": 4401.0,
        "swap": 0.0,
        "profit": 1.0,
        "magic": 0,
        "comment": comment,
    }


def audit_events(db):
    with session(db) as con:
        return [dict(row) for row in con.execute(
            "SELECT event_type,entity_id,payload_json FROM audit_events WHERE source='position_tracer' ORDER BY id"
        ).fetchall()]


def test_external_position_full_causal_trace(tmp_path):
    db = tmp_path / "forex.db"
    initialize(db)

    trace_external_positions(db, [raw_position()], observed_at_utc=NOW)
    events = audit_events(db)
    assert [row["event_type"] for row in events] == [
        "EXTERNAL_POSITION_DETECTED",
        "EXTERNAL_POSITION_UNPROTECTED",
    ]
    detected = json.loads(events[0]["payload_json"])
    assert detected["ownership"] == "EXTERNAL_OR_MANUAL"
    assert detected["reason"] == "NO_FOREX_AI_INTENT_MATCH"
    blocked = json.loads(events[1]["payload_json"])
    assert blocked["blocker"] == "UNPROTECTED_POSITION:1001"
    assert blocked["new_entries_blocked"] is True

    trace_external_positions(
        db,
        [raw_position(sl=4390.0, tp=4420.0)],
        observed_at_utc=NOW,
    )
    events = audit_events(db)
    assert events[-1]["event_type"] == "EXTERNAL_POSITION_PROTECTION_CHANGED"
    protection = json.loads(events[-1]["payload_json"])
    assert protection["protected"] is True
    assert protection["old_sl"] == "0"
    assert protection["new_sl"] == "4390.0"

    trace_external_positions(db, [], observed_at_utc=NOW)
    events = audit_events(db)
    assert events[-1]["event_type"] == "EXTERNAL_POSITION_CLOSED"
    with session(db) as con:
        row = con.execute("SELECT active FROM external_position_state_v1 WHERE ticket=1001").fetchone()
        assert int(row["active"]) == 0


def test_same_external_position_does_not_spam_detect_events(tmp_path):
    db = tmp_path / "forex.db"
    initialize(db)
    position = raw_position(sl=4390.0, tp=4420.0)
    trace_external_positions(db, [position], observed_at_utc=NOW)
    trace_external_positions(db, [position], observed_at_utc=NOW)
    events = audit_events(db)
    assert [row["event_type"] for row in events] == ["EXTERNAL_POSITION_DETECTED"]


def test_forex_ai_owned_position_is_not_classified_external(tmp_path):
    db = tmp_path / "forex.db"
    initialize(db)
    repo = SQLiteIntentRepository(db)
    intent = OrderIntent(
        intent_id="intent-owned-1",
        candidate_id="candidate-owned-1",
        idempotency_key="owned-key-1",
        symbol="XAUUSDc",
        side="BUY",
        volume=Decimal("0.05"),
        entry=Decimal("4400"),
        stop_loss=Decimal("4390"),
        take_profit=Decimal("4420"),
        state=ExecutionState.ACCEPTED,
        created_at_utc=NOW,
    )
    repo.save(intent)

    trace_external_positions(
        db,
        [raw_position(ticket=2002, sl=4390.0, tp=4420.0, comment=intent_comment(intent.intent_id))],
        observed_at_utc=NOW,
    )
    assert audit_events(db) == []
