from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forex_ai.journal.db import connect, log_audit_event

# MetaTrader 5 DEAL_ENTRY values.
ENTRY_EVENT = {
    0: "ENTRY_FILLED",
    1: "EXIT_FILLED",
    2: "POSITION_REVERSED",
    3: "EXIT_BY_FILLED",
}


def audit_mt5_deals(db_path, deals: list[dict[str, Any]]) -> int:
    """Append audit events for trade deals observed in MT5 history.

    Funding/balance/credit records have no traded symbol and are intentionally skipped.
    Deal ticket provides idempotency so repeated history synchronization cannot duplicate events.
    """
    created = 0
    for deal in deals:
        ticket = deal.get("ticket")
        symbol = str(deal.get("symbol") or "")
        volume = deal.get("volume")
        entry = deal.get("entry")
        if not ticket or not symbol or not isinstance(volume, (int, float)) or volume <= 0:
            continue
        event_type = ENTRY_EVENT.get(entry, "TRADE_DEAL")
        entity_id = str(ticket)
        with connect(db_path) as con:
            exists = con.execute(
                "SELECT 1 FROM audit_events WHERE source='mt5_reconcile' AND entity_id=? LIMIT 1",
                (entity_id,),
            ).fetchone()
        if exists:
            continue

        position_id = deal.get("position_id")
        correlation_id = f"mt5-position-{position_id}" if position_id else f"mt5-deal-{ticket}"
        mt5_time_msc = int(deal["time_msc"]) if isinstance(deal.get("time_msc"), (int, float)) else None
        mt5_time_utc = (
            datetime.fromtimestamp(mt5_time_msc / 1000, timezone.utc).isoformat(timespec="milliseconds")
            if mt5_time_msc is not None
            else None
        )
        log_audit_event(
            db_path,
            event_type=event_type,
            source="mt5_reconcile",
            symbol=symbol,
            entity_id=entity_id,
            correlation_id=correlation_id,
            market_time_msc=mt5_time_msc,
            payload={
                "deal_ticket": ticket,
                "order_ticket": deal.get("order"),
                "position_id": position_id,
                "entry": entry,
                "type": deal.get("type"),
                "volume": volume,
                "price": deal.get("price"),
                "commission": deal.get("commission"),
                "swap": deal.get("swap"),
                "profit": deal.get("profit"),
                "fee": deal.get("fee"),
                "reason": deal.get("reason"),
                "comment": deal.get("comment"),
                "mt5_time_seconds": deal.get("time"),
                "mt5_time_msc": deal.get("time_msc"),
                "mt5_time_utc": mt5_time_utc,
            },
        )
        created += 1
    return created
