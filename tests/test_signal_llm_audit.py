from forex_ai.journal.db import connect, initialize
from forex_ai.journal.repository import insert_llm_decision, insert_signal, pending_signals


def test_signal_and_llm_decision_share_correlation_and_precise_audit(tmp_path):
    db = tmp_path / "audit.db"
    initialize(db)
    signal_id, created = insert_signal(
        db,
        signal_key="abc123",
        symbol="EURUSDc",
        strategy="trend_pullback",
        direction="BUY",
        score=0.8,
        proposed_entry=1.1,
        proposed_sl=1.09,
        proposed_tp=1.12,
        rr=2.0,
        payload={"evidence": "test"},
        market_time_msc=1_788_408_400_123,
    )
    assert created is True
    assert [row["id"] for row in pending_signals(db)] == [signal_id]

    decision_id = insert_llm_decision(
        db,
        symbol="EURUSDc",
        mode="SHADOW",
        model="deepseek-v4-flash",
        prompt_version="reviewer-v1",
        decision={
            "action": "NO_TRADE",
            "confidence": 0.7,
            "thesis": "test",
            "risk_flags": ["TEST"],
        },
        usage={"input_tokens": 100, "output_tokens": 20, "api_cost_usd": 0.001},
        signal_id=signal_id,
        correlation_id="abc123",
        market_time_msc=1_788_408_400_123,
    )
    assert decision_id > 0
    assert pending_signals(db) == []

    with connect(db) as con:
        rows = con.execute(
            "select timestamp_utc,epoch_ms,correlation_id,event_type,market_time_msc from audit_events order by id"
        ).fetchall()
    assert [row["event_type"] for row in rows] == ["SIGNAL", "LLM_DECISION"]
    assert all(row["correlation_id"] == "abc123" for row in rows)
    assert all("T" in row["timestamp_utc"] and "+00:00" in row["timestamp_utc"] for row in rows)
    assert all(row["epoch_ms"] > 0 for row in rows)
    assert all(row["market_time_msc"] == 1_788_408_400_123 for row in rows)
