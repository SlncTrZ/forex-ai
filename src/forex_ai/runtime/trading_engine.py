from __future__ import annotations

from dataclasses import dataclass

from forex_ai.config import RuntimeConfig, load_risk_config, load_risk_profile
from forex_ai.integration.engine import DecisionOrchestrator
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize


@dataclass(frozen=True)
class IntegrationServices:
    decisions: DecisionOrchestrator
    execution: GuardedExecutionService


def build_integration_services(cfg: RuntimeConfig) -> IntegrationServices:
    """Build the production-v1 integration graph without arming execution.

    Database initialization is additive/idempotent. The execution service reads
    the persistent arming/kill-switch state immediately before broker preflight
    and send; merely building this graph can never arm trading.
    """
    initialize(cfg.db_path)
    profile = load_risk_profile()
    raw_risk = load_risk_config()
    execution_enabled = bool(raw_risk.get("execution_enabled", False))
    return IntegrationServices(
        decisions=DecisionOrchestrator(db_path=cfg.db_path, risk_profile=profile),
        execution=GuardedExecutionService(db_path=cfg.db_path, execution_enabled=execution_enabled),
    )
