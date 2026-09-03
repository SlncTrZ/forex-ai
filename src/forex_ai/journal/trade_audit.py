from __future__ import annotations

from typing import Any

from forex_ai.journal.db import log_audit_event


TRADE_EVENT_TYPES = {
    "ENTRY_REQUEST",
    "ENTRY_FILLED",
    "ENTRY_REJECTED",
    "EXIT_REQUEST",
    "EXIT_FILLED",
    "EXIT_REJECTED",
    "SL_TP_UPDATE",
    "RISK_REJECT",
}


def record_trade_event(
    db_path,
    *,
    event_type: str,
    correlation_id: str,
    symbol: str,
    payload: dict[str, Any],
    entity_id: str | None = None,
    market_time_msc: int | None = None,
    source: str = "execution",
) -> int:
    if event_type not in TRADE_EVENT_TYPES:
        raise ValueError(f"Unsupported trade audit event {event_type}")
    return log_audit_event(
        db_path,
        event_type=event_type,
        source=source,
        symbol=symbol,
        entity_id=entity_id,
        correlation_id=correlation_id,
        market_time_msc=market_time_msc,
        payload=payload,
    )
