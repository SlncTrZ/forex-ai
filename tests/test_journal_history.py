from forex_ai.journal.db import connect, initialize
from forex_ai.journal.repository import upsert_mt5_deals, upsert_mt5_orders


def test_history_tables_upsert(tmp_path):
    db = tmp_path / "test.db"
    initialize(db)

    deals = [{
        "ticket": 1, "order": 2, "time": 100, "time_msc": 100000,
        "type": 0, "entry": 0, "magic": 0, "position_id": 3,
        "symbol": "EURUSDc", "volume": 0.01, "price": 1.1,
        "commission": 0.0, "swap": 0.0, "profit": 1.0, "fee": 0.0,
        "reason": 0, "comment": "test",
    }]
    orders = [{
        "ticket": 2, "time_setup": 99, "time_done": 100, "type": 0,
        "state": 4, "magic": 0, "position_id": 3, "position_by_id": 0,
        "symbol": "EURUSDc", "volume_initial": 0.01, "volume_current": 0.0,
        "price_open": 1.1, "sl": 1.09, "tp": 1.12, "price_current": 1.1,
        "reason": 0, "comment": "test",
    }]

    assert upsert_mt5_deals(db, deals) == 1
    assert upsert_mt5_orders(db, orders) == 1
    assert upsert_mt5_deals(db, deals) == 1

    with connect(db) as con:
        assert con.execute("select count(*) from mt5_deals").fetchone()[0] == 1
        assert con.execute("select count(*) from mt5_orders_history").fetchone()[0] == 1
        assert con.execute("select value from schema_meta where key='schema_version'").fetchone()[0] == "5"
