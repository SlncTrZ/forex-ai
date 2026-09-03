from __future__ import annotations

from typing import Any

from forex_ai.journal.db import connect


def select_lessons(db_path, *, symbol: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    query = """
        SELECT id,timestamp,trade_id,symbol,setup,regime,lesson_type,lesson_text,evidence_json,confidence
        FROM lessons
        WHERE active=1 AND (symbol IS NULL OR symbol=? OR ? IS NULL)
        ORDER BY COALESCE(confidence,0) DESC, id DESC
        LIMIT ?
    """
    with connect(db_path) as con:
        rows = con.execute(query, (symbol, symbol, limit)).fetchall()
        return [dict(row) for row in rows]
