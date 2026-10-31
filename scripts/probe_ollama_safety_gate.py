#!/usr/bin/env python3
"""Read-only Ollama connectivity + structured-decision smoke test. No MT5 calls."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# Keep project code ahead of installed runtime releases.
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))

from forex_ai.advisory.ollama_safety_gate import GateUnavailable, OllamaSafetyGate
from forex_ai.execution.state import ExecutionState, OrderIntent


def main() -> int:
    try:
        model = OllamaSafetyGate.from_environment()
        now = datetime.now(timezone.utc)
        # Synthetic example: never a production candidate, never sent to MT5.
        intent = OrderIntent(
            intent_id="probe-ollama-safety-gate", candidate_id="synthetic-safety-probe",
            idempotency_key="probe-no-trading", symbol="EURUSD", side="BUY",
            volume=Decimal("0.01"), entry=Decimal("1.10000"),
            stop_loss=Decimal("1.09000"), take_profit=Decimal("1.12000"),
            state=ExecutionState.PREFLIGHT_PASSED, created_at_utc=now,
        )
        verdict = model(intent, {
            "symbol": "EURUSD", "volume": 0.01,
            "price": 1.1, "sl": 1.09, "tp": 1.12,
        }, context={"strategy_id": "SYNTHETIC_PROBE", "account_trade_mode": "DEMO"})
        print(json.dumps({
            "status": "MODEL_STRUCTURED_OUTPUT_VALID",
            "allowed": verdict.allowed,
            "reason_code": verdict.reason_code,
            "latency_ms": verdict.latency_ms,
            "model_fingerprint": verdict.model_fingerprint,
            "sent_broker_orders": 0,
        }))
        return 0
    except GateUnavailable as exc:
        print(json.dumps({"status": "AI_GATE_UNAVAILABLE", "reason": str(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
