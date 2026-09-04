from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from forex_ai.execution.mt5 import intent_comment
from forex_ai.execution.state import ExecutionState
from forex_ai.journal.db import log_audit_event, session

UTC = timezone.utc


def _side(raw: dict[str, Any]) -> str:
    return "BUY" if int(raw.get("type") or 0) == 0 else "SELL"


def _owned_position_identity(db_path: Path) -> tuple[set[int], set[str]]:
    with session(db_path) as con:
        rows = con.execute(
            "SELECT intent_id,broker_position_ticket,state FROM order_intents_v1"
        ).fetchall()
    tickets: set[int] = set()
    comments: set[str] = set()
    terminal = {ExecutionState.REJECTED.value, ExecutionState.CANCELLED.value, ExecutionState.CLOSED.value}
    for row in rows:
        if row["broker_position_ticket"] is not None:
            tickets.add(int(row["broker_position_ticket"]))
        if str(row["state"]) not in terminal:
            comments.add(intent_comment(str(row["intent_id"])))
    return tickets, comments


def trace_external_positions(
    db_path: Path,
    positions: list[dict[str, Any]],
    *,
    observed_at_utc: datetime,
) -> None:
    now = observed_at_utc.astimezone(UTC).isoformat()
    owned_tickets, owned_comments = _owned_position_identity(db_path)
    current: dict[int, dict[str, Any]] = {}
    for raw in positions:
        ticket = int(raw.get("ticket") or 0)
        comment = str(raw.get("comment") or "")
        if ticket <= 0 or ticket in owned_tickets or (comment and comment in owned_comments):
            continue
        current[ticket] = raw

    events: list[tuple[str, str, int, dict[str, Any]]] = []
    with session(db_path) as con:
        prior_rows = {
            int(row["ticket"]): row
            for row in con.execute("SELECT * FROM external_position_state_v1 WHERE active=1").fetchall()
        }

        for ticket, raw in current.items():
            symbol = str(raw.get("symbol") or "")
            side = _side(raw)
            volume = str(Decimal(str(raw.get("volume") or 0)))
            sl = str(Decimal(str(raw.get("sl") or 0)))
            tp = str(Decimal(str(raw.get("tp") or 0)))
            magic = int(raw["magic"]) if raw.get("magic") is not None else None
            comment = str(raw.get("comment") or "")
            payload = json.dumps(raw, ensure_ascii=False, default=str, sort_keys=True)
            prior = prior_rows.get(ticket)

            if prior is None:
                con.execute(
                    """INSERT INTO external_position_state_v1(
                        ticket,symbol,side,volume,sl,tp,magic,comment,ownership,
                        first_seen_at_utc,last_seen_at_utc,active,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ticket,
                        symbol,
                        side,
                        volume,
                        sl,
                        tp,
                        magic,
                        comment,
                        "EXTERNAL_OR_MANUAL",
                        now,
                        now,
                        1,
                        payload,
                    ),
                )
                events.append((
                    "EXTERNAL_POSITION_DETECTED",
                    symbol,
                    ticket,
                    {
                        "ticket": ticket,
                        "ownership": "EXTERNAL_OR_MANUAL",
                        "reason": "NO_FOREX_AI_INTENT_MATCH",
                        "side": side,
                        "volume": volume,
                        "sl": sl,
                        "tp": tp,
                        "magic": magic,
                        "comment": comment,
                    },
                ))
                if Decimal(sl) <= 0 or Decimal(tp) <= 0:
                    events.append((
                        "EXTERNAL_POSITION_UNPROTECTED",
                        symbol,
                        ticket,
                        {
                            "ticket": ticket,
                            "ownership": "EXTERNAL_OR_MANUAL",
                            "blocker": f"UNPROTECTED_POSITION:{ticket}",
                            "new_entries_blocked": True,
                            "sl": sl,
                            "tp": tp,
                        },
                    ))
            else:
                protection_changed = str(prior["sl"]) != sl or str(prior["tp"]) != tp
                volume_changed = str(prior["volume"]) != volume
                con.execute(
                    """UPDATE external_position_state_v1
                       SET symbol=?,side=?,volume=?,sl=?,tp=?,magic=?,comment=?,
                           last_seen_at_utc=?,active=1,payload_json=?
                       WHERE ticket=?""",
                    (symbol, side, volume, sl, tp, magic, comment, now, payload, ticket),
                )
                if protection_changed:
                    events.append((
                        "EXTERNAL_POSITION_PROTECTION_CHANGED",
                        symbol,
                        ticket,
                        {
                            "ticket": ticket,
                            "ownership": "EXTERNAL_OR_MANUAL",
                            "old_sl": str(prior["sl"]),
                            "old_tp": str(prior["tp"]),
                            "new_sl": sl,
                            "new_tp": tp,
                            "protected": Decimal(sl) > 0 and Decimal(tp) > 0,
                        },
                    ))
                if volume_changed:
                    events.append((
                        "EXTERNAL_POSITION_VOLUME_CHANGED",
                        symbol,
                        ticket,
                        {
                            "ticket": ticket,
                            "ownership": "EXTERNAL_OR_MANUAL",
                            "old_volume": str(prior["volume"]),
                            "new_volume": volume,
                        },
                    ))

        for ticket, prior in prior_rows.items():
            if ticket in current:
                continue
            con.execute(
                "UPDATE external_position_state_v1 SET active=0,last_seen_at_utc=? WHERE ticket=?",
                (now, ticket),
            )
            events.append((
                "EXTERNAL_POSITION_CLOSED",
                str(prior["symbol"]),
                ticket,
                {
                    "ticket": ticket,
                    "ownership": "EXTERNAL_OR_MANUAL",
                    "last_known_volume": str(prior["volume"]),
                    "last_known_sl": str(prior["sl"]),
                    "last_known_tp": str(prior["tp"]),
                },
            ))

    for event_type, symbol, ticket, payload in events:
        log_audit_event(
            db_path,
            event_type=event_type,
            source="position_tracer",
            symbol=symbol,
            entity_id=str(ticket),
            payload=payload,
        )
