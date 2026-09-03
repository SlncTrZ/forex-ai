from forex_ai.intelligence.budget import get_budget_status
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.repository import insert_llm_decision


def test_budget_counts_success_and_failed_paid_calls(tmp_path):
    db = tmp_path / "budget.db"
    initialize(db)
    cfg = {"invocation": {"max_calls_per_day": 10, "max_cost_usd_per_day": 1.0}}

    insert_llm_decision(
        db,
        symbol="EURUSDc",
        mode="SHADOW",
        model="deepseek-v4-flash",
        prompt_version="reviewer-v1",
        decision={"action": "NO_TRADE", "confidence": 0.5, "thesis": "x", "risk_flags": []},
        usage={"api_cost_usd": 0.01},
    )
    log_audit_event(
        db,
        event_type="LLM_ERROR",
        source="llm",
        symbol="EURUSDc",
        payload={"usage": {"api_cost_usd": 0.02}, "error": "incomplete"},
    )

    status = get_budget_status(db, cfg)
    assert status.calls_today == 2
    assert abs(status.cost_today_usd - 0.03) < 1e-12
    assert status.allowed is True
