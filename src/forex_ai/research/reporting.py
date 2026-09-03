from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .evaluation import EvaluationMetrics, evaluate_trades, grouped_metrics
from .replay import ReplayTrade


def utc_session(hour: int) -> str:
    if 0 <= hour < 7:
        return "ASIA"
    if 7 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 16:
        return "LONDON_NEW_YORK_OVERLAP"
    if 16 <= hour < 21:
        return "NEW_YORK"
    return "OFF_HOURS"


@dataclass(frozen=True)
class SplitComparison:
    in_sample: EvaluationMetrics
    validation: EvaluationMetrics
    final_test: EvaluationMetrics
    validation_expectancy_delta_r: float
    test_expectancy_delta_r: float


def compare_splits(*, train: Iterable[ReplayTrade], validation: Iterable[ReplayTrade], final_test: Iterable[ReplayTrade]) -> SplitComparison:
    train_metrics = evaluate_trades(train)
    validation_metrics = evaluate_trades(validation)
    test_metrics = evaluate_trades(final_test)
    return SplitComparison(
        train_metrics,
        validation_metrics,
        test_metrics,
        validation_metrics.expectancy_r - train_metrics.expectancy_r,
        test_metrics.expectancy_r - train_metrics.expectancy_r,
    )


def performance_breakdown(
    trades: Iterable[ReplayTrade], *, volatility_regime_by_candidate: Mapping[str, str] | None = None
) -> dict[str, dict[str, EvaluationMetrics]]:
    rows = tuple(trades)
    by_session: dict[str, list[ReplayTrade]] = {}
    by_volatility: dict[str, list[ReplayTrade]] = {}
    volatility_regime_by_candidate = volatility_regime_by_candidate or {}
    for trade in rows:
        by_session.setdefault(utc_session(trade.entry_time_utc.hour), []).append(trade)
        regime = volatility_regime_by_candidate.get(trade.candidate_id, "UNSPECIFIED")
        by_volatility.setdefault(regime, []).append(trade)
    return {
        "symbol": grouped_metrics(rows, "symbol"),
        "direction": grouped_metrics(rows, "side"),
        "strategy": grouped_metrics(rows, "strategy_id"),
        "session": {key: evaluate_trades(group) for key, group in by_session.items()},
        "volatility_regime": {key: evaluate_trades(group) for key, group in by_volatility.items()},
    }
