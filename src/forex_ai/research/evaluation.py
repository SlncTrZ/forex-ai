from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import mean
from typing import Iterable

from .replay import ReplayTrade


@dataclass(frozen=True)
class EvaluationMetrics:
    trade_count: int
    expectancy_r: float
    expectancy_account_currency: float
    win_rate: float
    payoff_ratio: float
    profit_factor: float
    max_drawdown_account_currency: float
    max_drawdown_duration: int
    tail_loss_r: float
    turnover: float
    exposure_seconds: float
    ci_expectancy_r_95: tuple[float, float]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _bootstrap_ci(values: list[float], samples: int = 500, seed: int = 17) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = [mean(rng.choice(values) for _ in values) for _ in range(samples)]
    return (_percentile(means, 0.025), _percentile(means, 0.975))


def evaluate_trades(trades: Iterable[ReplayTrade]) -> EvaluationMetrics:
    rows = tuple(trades)
    if not rows:
        return EvaluationMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, (0.0, 0.0))
    rs = [trade.net_r for trade in rows]
    pnls = [trade.pnl_account_currency for trade in rows]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    avg_win = mean(wins) if wins else 0.0
    avg_loss = abs(mean(losses)) if losses else 0.0
    payoff = avg_win / avg_loss if avg_loss else (math.inf if avg_win else 0.0)
    gross_profit = sum(max(p, 0.0) for p in pnls)
    gross_loss = abs(sum(min(p, 0.0) for p in pnls))
    profit_factor = gross_profit / gross_loss if gross_loss else (math.inf if gross_profit else 0.0)

    equity = peak = 0.0
    max_dd = 0.0
    current_duration = max_duration = 0
    for pnl in pnls:
        equity += pnl
        if equity >= peak:
            peak = equity
            current_duration = 0
        else:
            current_duration += 1
            max_duration = max(max_duration, current_duration)
            max_dd = max(max_dd, peak - equity)

    exposure = sum(max((t.exit_time_utc - t.entry_time_utc).total_seconds(), 0.0) for t in rows)
    turnover = sum(abs(t.entry_price) + abs(t.exit_price) for t in rows)
    return EvaluationMetrics(
        len(rows), mean(rs), mean(pnls), len(wins) / len(rows), payoff, profit_factor,
        max_dd, max_duration, _percentile(rs, 0.05), turnover, exposure, _bootstrap_ci(rs),
    )


def grouped_metrics(trades: Iterable[ReplayTrade], key: str) -> dict[str, EvaluationMetrics]:
    groups: dict[str, list[ReplayTrade]] = {}
    for trade in trades:
        value = getattr(trade, key)
        groups.setdefault(str(value), []).append(trade)
    return {name: evaluate_trades(group) for name, group in groups.items()}
