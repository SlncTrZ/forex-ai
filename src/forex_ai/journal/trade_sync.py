"""Reconstruct closed trades from mt5_deals and sync into trades table."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from forex_ai.journal.db import connect


def sync_trades_from_deals(db_path) -> int:
    """Aggregate entry and exit deals into completed trades.

    A completed trade has at least one entry deal (entry=0) and one exit deal (entry in (1, 3)).
    Position_id maps 1-to-1 to mt5_position_id in the trades table.
    """
    with connect(db_path) as con:
        # Find all closed positions where an exit deal exists
        closed_pos_rows = con.execute(
            """SELECT position_id, symbol
               FROM mt5_deals
               WHERE position_id IS NOT NULL AND position_id > 0 AND entry IN (1, 3)
               GROUP BY position_id, symbol"""
        ).fetchall()

        if not closed_pos_rows:
            return 0

        synced = 0
        for pos_id, symbol in closed_pos_rows:
            # Check if trade already exists
            existing = con.execute(
                "SELECT id FROM trades WHERE mt5_position_id=?", (pos_id,)
            ).fetchone()
            if existing is not None:
                continue

            # Fetch all deals for this position sorted by time
            deals = con.execute(
                """SELECT ticket, time, time_msc, type, entry, volume, price, profit, commission, swap, fee, magic, comment
                   FROM mt5_deals
                   WHERE position_id=?
                   ORDER BY time_msc ASC, ticket ASC""",
                (pos_id,),
            ).fetchall()

            entries = [d for d in deals if d["entry"] == 0]
            exits = [d for d in deals if d["entry"] in (1, 3)]

            if not entries or not exits:
                continue

            first_entry = entries[0]
            last_exit = exits[-1]

            open_time = datetime.fromtimestamp(first_entry["time"], timezone.utc).isoformat()
            close_time = datetime.fromtimestamp(last_exit["time"], timezone.utc).isoformat()

            entry_price = float(first_entry["price"])
            exit_price = float(last_exit["price"])
            volume = float(sum(d["volume"] for d in entries))

            gross_pnl = float(sum(d["profit"] for d in deals))
            commission = float(sum(d["commission"] + d["fee"] for d in deals))
            swap = float(sum(d["swap"] for d in deals))
            net_pnl = gross_pnl + commission + swap

            magic = first_entry["magic"]
            source_mode = "system" if magic == 260904 else "manual"

            payload = {
                "magic": magic,
                "entry_tickets": [d["ticket"] for d in entries],
                "exit_tickets": [d["ticket"] for d in exits],
                "comment": last_exit["comment"] or first_entry["comment"],
            }

            con.execute(
                """INSERT INTO trades(
                    symbol, source_mode, open_time, close_time, entry_price, exit_price,
                    volume, gross_pnl, commission, swap, net_pnl, mt5_position_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    source_mode,
                    open_time,
                    close_time,
                    entry_price,
                    exit_price,
                    volume,
                    gross_pnl,
                    commission,
                    swap,
                    net_pnl,
                    pos_id,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            synced += 1

    return synced
