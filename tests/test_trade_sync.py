from decimal import Decimal
from forex_ai.journal.db import connect
from forex_ai.journal.trade_sync import sync_trades_from_deals


def test_sync_trades_from_deals(tmp_path):
    db_file = tmp_path / "test.db"
    with connect(db_file) as con:
        con.execute(
            """CREATE TABLE mt5_deals (
                ticket INTEGER PRIMARY KEY,
                order_id INTEGER,
                time INTEGER,
                time_msc INTEGER,
                type INTEGER,
                entry INTEGER,
                magic INTEGER,
                position_id INTEGER,
                reason INTEGER,
                volume REAL,
                price REAL,
                commission REAL,
                swap REAL,
                profit REAL,
                fee REAL,
                symbol TEXT,
                comment TEXT,
                raw_json TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE trades (
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
            )"""
        )
        # Position 101: 1 Entry deal, 1 Exit deal
        con.execute(
            """INSERT INTO mt5_deals VALUES
            (1, 1, 1700000000, 1700000000000, 0, 0, 260904, 101, 0, 0.01, 2000.0, -0.05, 0.0, 0.0, 0.0, 'XAUUSD', 'system entry', '{}'),
            (2, 2, 1700001000, 1700001000000, 1, 1, 260904, 101, 0, 0.01, 2010.0, -0.05, 0.0, 10.0, 0.0, 'XAUUSD', 'system exit', '{}')"""
        )

    synced = sync_trades_from_deals(db_file)
    assert synced == 1

    with connect(db_file) as con:
        row = con.execute("SELECT * FROM trades WHERE mt5_position_id=101").fetchone()
        assert row is not None
        assert row["symbol"] == "XAUUSD"
        assert row["source_mode"] == "system"
        assert row["entry_price"] == 2000.0
        assert row["exit_price"] == 2010.0
        assert row["gross_pnl"] == 10.0
        assert row["net_pnl"] == 9.9
