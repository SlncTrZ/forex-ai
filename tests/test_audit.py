from datetime import datetime

from forex_ai.journal.db import connect, initialize, log_audit_event
from forex_ai.journal.trade_audit import record_trade_event


def test_audit_event_has_precise_time_and_correlation(tmp_path):
    db = tmp_path / "audit.db"
    initialize(db)
    event_id = record_trade_event(
        db,
        event_type="ENTRY_REQUEST",
        correlation_id="sig-abc",
        symbol="EURUSDc",
        market_time_msc=1_788_408_438_123,
        payload={"volume": 0.01},
    )
    with connect(db) as con:
        row = con.execute(
            "select timestamp_utc,epoch_ms,correlation_id,event_type,symbol,market_time_msc from audit_events where id=?",
            (event_id,),
        ).fetchone()
    parsed = datetime.fromisoformat(row["timestamp_utc"])
    assert parsed.tzinfo is not None
    assert parsed.microsecond >= 0
    assert row["epoch_ms"] > 0
    assert row["correlation_id"] == "sig-abc"
    assert row["event_type"] == "ENTRY_REQUEST"
    assert row["symbol"] == "EURUSDc"
    assert row["market_time_msc"] == 1_788_408_438_123


def test_generic_llm_context_event(tmp_path):
    db = tmp_path / "audit.db"
    initialize(db)
    log_audit_event(
        db,
        event_type="LLM_CONTEXT",
        source="llm",
        correlation_id="sig-xyz",
        symbol="XAUUSDc",
        payload={"clock": "authoritative"},
    )
    with connect(db) as con:
        row = con.execute("select event_type,source,payload_json from audit_events").fetchone()
    assert row["event_type"] == "LLM_CONTEXT"
    assert row["source"] == "llm"
    assert "authoritative" in row["payload_json"]
