"""Monte Carlo Analysis — Sequence permutation, bootstrap resample, and risk-of-ruin bounds.
Wing: code | Topic: monte_carlo | Updated: 2026-10-28 12:28
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Sequence

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


@dataclass(frozen=True)
class MonteCarloRuinReport:
    iterations: int
    trade_count: int
    ruin_threshold_r: float
    risk_of_ruin_pct: float
    max_drawdown_r_median: float
    max_drawdown_r_p95: float
    max_drawdown_r_p99: float


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


def permute_trade_sequences_with_ruin(
    trades: Sequence[float | ReplayTrade],
    *,
    ruin_threshold_r: float = 10.0,
    iterations: int = 10_000,
    seed: int = 42,
) -> MonteCarloRuinReport:
    """Hoán vị không hoàn lại thứ tự xuất hiện của các lệnh (Trade Sequence Shuffling).
    Mô phỏng 10,000 kịch bản thực tế để đo xác suất sụt giảm vượt ngưỡng thảm họa (Risk of Ruin).
    """
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    if not trades:
        return MonteCarloRuinReport(iterations, 0, ruin_threshold_r, 0.0, 0.0, 0.0, 0.0)

    # Trích xuất net_r nếu là ReplayTrade, ngược lại giữ nguyên float
    values = [t.net_r if hasattr(t, "net_r") else float(t) for t in trades]
    n = len(values)
    rng = random.Random(seed)

    drawdowns: list[float] = []
    ruin_count = 0

    for _ in range(iterations):
        shuffled = list(values)
        rng.shuffle(shuffled)
        dd = _max_drawdown(shuffled)
        drawdowns.append(dd)
        if dd >= ruin_threshold_r:
            ruin_count += 1

    return MonteCarloRuinReport(
        iterations=iterations,
        trade_count=n,
        ruin_threshold_r=ruin_threshold_r,
        risk_of_ruin_pct=(ruin_count / iterations) * 100.0,
        max_drawdown_r_median=_percentile(drawdowns, 0.50),
        max_drawdown_r_p95=_percentile(drawdowns, 0.95),
        max_drawdown_r_p99=_percentile(drawdowns, 0.99),
    )
