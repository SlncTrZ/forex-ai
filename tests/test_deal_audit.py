from forex_ai.journal.db import connect, initialize
from forex_ai.journal.deal_audit import audit_mt5_deals


def test_mt5_trade_deals_become_idempotent_entry_exit_audit(tmp_path):
    db = tmp_path / "audit.db"
    initialize(db)
    deals = [
        {
            "ticket": 101,
            "order": 201,
            "time": 1_788_408_400,
            "time_msc": 1_788_408_400_123,
            "type": 0,
            "entry": 0,
            "position_id": 301,
            "symbol": "EURUSDc",
            "volume": 0.01,
            "price": 1.16,
            "commission": 0,
            "swap": 0,
            "profit": 0,
            "fee": 0,
            "reason": 3,
            "comment": "test",
        },
        {
            "ticket": 102,
            "order": 202,
            "time": 1_788_408_500,
            "time_msc": 1_788_408_500_456,
            "type": 1,
            "entry": 1,
            "position_id": 301,
            "symbol": "EURUSDc",
            "volume": 0.01,
            "price": 1.161,
            "commission": 0,
            "swap": 0,
            "profit": 1,
            "fee": 0,
            "reason": 3,
            "comment": "test",
        },
    ]
    assert audit_mt5_deals(db, deals) == 2
    assert audit_mt5_deals(db, deals) == 0
    with connect(db) as con:
        rows = con.execute(
            "select event_type,correlation_id,market_time_msc from audit_events order by id"
        ).fetchall()
    assert [row["event_type"] for row in rows] == ["ENTRY_FILLED", "EXIT_FILLED"]
    assert rows[0]["correlation_id"] == "mt5-position-301"
    assert rows[1]["correlation_id"] == "mt5-position-301"
    assert rows[0]["market_time_msc"] == 1_788_408_400_123
