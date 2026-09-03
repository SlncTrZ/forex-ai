from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from forex_ai.strategy.v1.contracts import MarketSnapshot, StrategyConfig

from .evaluation import EvaluationMetrics, evaluate_trades
from .replay import CostModel, ReplayEngine, ReplayEvent, StrategyCallable


@dataclass(frozen=True)
class SensitivityPoint:
    cost_model_fingerprint: str
    metrics: EvaluationMetrics
    candidate_count: int
    rejected_count: int


def cost_sensitivity(
    *,
    strategy: StrategyCallable,
    config: StrategyConfig,
    events: Iterable[ReplayEvent],
    cost_models: Iterable[CostModel],
    account_r_value: float = 1.0,
) -> tuple[SensitivityPoint, ...]:
    frozen_events = tuple(events)
    points = []
    for cost_model in cost_models:
        artifact = ReplayEngine(strategy, config, cost_model).run(frozen_events, account_r_value=account_r_value)
        points.append(
            SensitivityPoint(
                artifact.cost_model_fingerprint,
                evaluate_trades(artifact.trades),
                artifact.candidate_count,
                artifact.rejected_count,
            )
        )
    return tuple(points)
