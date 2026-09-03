from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from forex_ai.journal.db import connect


@dataclass(frozen=True)
class BudgetStatus:
    allowed: bool
    calls_today: int
    cost_today_usd: float
    max_calls_per_day: int
    max_cost_usd_per_day: float
    reason: str | None = None


def get_budget_status(db_path, llm_config: dict[str, Any]) -> BudgetStatus:
    invocation = llm_config.get("invocation", {})
    max_calls = int(invocation.get("max_calls_per_day", 100))
    max_cost = float(invocation.get("max_cost_usd_per_day", 0.25))
    day_prefix = datetime.now(timezone.utc).date().isoformat() + "%"
    with connect(db_path) as con:
        decision_row = con.execute(
            """SELECT count(*) AS calls, COALESCE(sum(api_cost_usd),0) AS cost
               FROM llm_decisions WHERE timestamp LIKE ? AND model LIKE 'deepseek-%'""",
            (day_prefix,),
        ).fetchone()
        error_row = con.execute(
            """SELECT count(*) AS calls,
                      COALESCE(sum(CAST(json_extract(payload_json,'$.usage.api_cost_usd') AS REAL)),0) AS cost
               FROM audit_events
               WHERE event_type='LLM_ERROR' AND timestamp_utc LIKE ?""",
            (day_prefix,),
        ).fetchone()
    calls = int(decision_row["calls"]) + int(error_row["calls"])
    cost = float(decision_row["cost"]) + float(error_row["cost"])
    reason = None
    if calls >= max_calls:
        reason = "MAX_CALLS_PER_DAY"
    elif cost >= max_cost:
        reason = "MAX_COST_PER_DAY"
    return BudgetStatus(
        allowed=reason is None,
        calls_today=calls,
        cost_today_usd=cost,
        max_calls_per_day=max_calls,
        max_cost_usd_per_day=max_cost,
        reason=reason,
    )
