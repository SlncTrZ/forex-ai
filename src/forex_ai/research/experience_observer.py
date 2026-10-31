"""Bounded read-only-source historical retrieval batch, writes research audit only.

No Ollama inference, no trade-side effects. Keep P2 History = 0 until
fully featured, independently calibrated outcomes become available.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.journal.db import log_audit_event, session
from forex_ai.research.experience_memory import POLICY_VERSION, read_experience

EVENT = "HISTORICAL_EXPERIENCE_RESEARCH"
MAX_CANDIDATES = 48
SOURCE = "historical_research_observer"


def scan_recent_experiences(db_path: Path, *, now_utc: datetime,
                            limit: int = 24) -> list[dict]:
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise ValueError("INVALID_RESEARCH_LIMIT")
    now = now_utc.astimezone(timezone.utc)
    with session(db_path) as con:
        ids = [r[0] for r in con.execute(
            "SELECT candidate_id FROM candidate_decisions "
            "ORDER BY generated_at_utc DESC LIMIT ?", (limit,),
        ).fetchall()]
    results = []
    for candidate_id in ids:
        item = read_experience(db_path, candidate_id, now_utc=now)
        if item["state"] in ("NOT_FOUND", "FUTURE_CANDIDATE"):
            continue
        # Candidate identity is stable, but historical broker deals may be
        # backfilled/corrected later. New verified evidence gets a new audit
        # fingerprint; identical evidence remains idempotent.
        record = {
            "policy_version": POLICY_VERSION, "candidate_id": candidate_id,
            "strategy_id": item["strategy_id"], "symbol": item["symbol"],
            "side": item["side"], "as_of_utc": item["as_of_utc"],
            "state": item["state"], "evidence_class": item["evidence_class"],
            "sample_size": item["sample_size"],
            "wins": item["wins"], "losses": item["losses"],
            "calibrated": False, "history_score_points": 0,
            "diagnostic_only": True,
            "top_matches": item["matches"][:5],
        }
        entity_id = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with session(db_path) as con:
            found = con.execute(
                "SELECT 1 FROM audit_events WHERE event_type=? AND entity_id=? LIMIT 1",
                (EVENT, entity_id),
            ).fetchone()
        if found is None:
            log_audit_event(
                db_path, event_type=EVENT, source=SOURCE, symbol=item["symbol"],
                entity_id=entity_id, payload=record,
            )
        results.append({
            "candidate_id": candidate_id, "symbol": item["symbol"],
            "strategy_id": item["strategy_id"],
            "state": item["state"], "sample_size": item["sample_size"],
            "calibrated": False, "history_score_points": 0,
        })
    return results
