from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from forex_ai.config import RuntimeConfig, load_execution_enabled, load_risk_profile
from forex_ai.integration.engine import DecisionOrchestrator
from forex_ai.integration.execution import GuardedExecutionService
from forex_ai.journal.db import initialize


@dataclass(frozen=True)
class IntegrationServices:
    decisions: DecisionOrchestrator
    execution: GuardedExecutionService


def build_integration_services(
    cfg: RuntimeConfig,
    *,
    identity_guard: Callable[[], None] | None = None,
) -> IntegrationServices:
    """Build the production-v1 integration graph without arming execution.

    Database initialization is additive/idempotent. The execution service reads
    the persistent arming/kill-switch state immediately before broker preflight
    and send; merely building this graph can never arm trading.
    """
    initialize(cfg.db_path)
    profile = load_risk_profile()
    execution_enabled = load_execution_enabled()
    return IntegrationServices(
        decisions=DecisionOrchestrator(db_path=cfg.db_path, risk_profile=profile),
        execution=GuardedExecutionService(
            db_path=cfg.db_path,
            execution_enabled=execution_enabled,
            identity_guard=identity_guard,
        ),
    )
