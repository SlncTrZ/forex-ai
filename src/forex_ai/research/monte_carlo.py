from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean
from typing import Iterable

from .evaluation import _percentile
from .replay import ReplayTrade


@dataclass(frozen=True)
class MonteCarloResult:
    samples: int
    trade_count: int
    expectancy_r_median: float
    expectancy_r_p05: float
    expectancy_r_p95: float
    max_drawdown_r_median: float
    max_drawdown_r_p95: float
    probability_positive_expectancy: float
    probability_net_positive: float


def _max_drawdown(values: list[float]) -> float:
    equity = peak = max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def bootstrap_trade_monte_carlo(
    trades: Iterable[ReplayTrade], *, samples: int = 2000, seed: int = 20260903
) -> MonteCarloResult:
    rows = tuple(trades)
    if samples <= 0:
        raise ValueError("samples must be positive")
    if not rows:
        return MonteCarloResult(samples, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    values = [trade.net_r for trade in rows]
    rng = random.Random(seed)
    means: list[float] = []
    drawdowns: list[float] = []
    net_positive = 0
    for _ in range(samples):
        path = [rng.choice(values) for _ in values]
        path_mean = mean(path)
        means.append(path_mean)
        drawdowns.append(_max_drawdown(path))
        if sum(path) > 0:
            net_positive += 1
    return MonteCarloResult(
        samples=samples,
        trade_count=len(values),
        expectancy_r_median=_percentile(means, 0.50),
        expectancy_r_p05=_percentile(means, 0.05),
        expectancy_r_p95=_percentile(means, 0.95),
        max_drawdown_r_median=_percentile(drawdowns, 0.50),
        max_drawdown_r_p95=_percentile(drawdowns, 0.95),
        probability_positive_expectancy=sum(value > 0 for value in means) / samples,
        probability_net_positive=net_positive / samples,
    )
