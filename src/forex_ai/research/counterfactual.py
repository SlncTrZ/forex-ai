from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class CounterfactualRecord:
    candidate_id: str
    symbol: str
    strategy_id: str
    advisory_action: str
    risk_multiplier: float
    hypothetical_entry: float
    stop_loss: float
    take_profit: float
    hypothetical_result_r: float
    provider_fingerprint: str
    latency_ms: int
    api_cost: float
    reason: str = ""


@dataclass(frozen=True)
class CounterfactualReport:
    technical_candidate_count: int
    consultation_count: int
    veto_count: int
    reduction_count: int
    veto_rate: float
    false_veto_rate: float
    expectancy_all_r: float
    expectancy_vetoed_r: float
    expectancy_kept_r: float
    incremental_expectancy_r: float
    api_cost_total: float
    api_cost_per_reviewed: float
    net_incremental_value_r: float


def evaluate(records: Iterable[CounterfactualRecord], *, api_cost_to_r: float = 0.0) -> CounterfactualReport:
    rows = tuple(records)
    if not rows:
        return CounterfactualReport(0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    reviewed = [r for r in rows if r.advisory_action in {"NO_CHANGE", "REDUCE_RISK", "VETO"}]
    vetoed = [r for r in reviewed if r.advisory_action == "VETO"]
    reduced = [r for r in reviewed if r.advisory_action == "REDUCE_RISK"]
    kept = [r for r in reviewed if r.advisory_action != "VETO"]
    all_exp = mean(r.hypothetical_result_r for r in rows)
    veto_exp = mean(r.hypothetical_result_r for r in vetoed) if vetoed else 0.0
    kept_exp = mean(r.hypothetical_result_r * r.risk_multiplier for r in kept) if kept else 0.0
    false_veto = sum(1 for r in vetoed if r.hypothetical_result_r > 0) / len(vetoed) if vetoed else 0.0
    # Incremental effect versus BOT_ONLY baseline: veto removes full R outcome; reduction scales it.
    advised_outcomes = [0.0 if r.advisory_action == "VETO" else r.hypothetical_result_r * r.risk_multiplier for r in reviewed]
    baseline_outcomes = [r.hypothetical_result_r for r in reviewed]
    incremental = (mean(advised_outcomes) - mean(baseline_outcomes)) if reviewed else 0.0
    total_cost = sum(r.api_cost for r in reviewed)
    per_review = total_cost / len(reviewed) if reviewed else 0.0
    return CounterfactualReport(
        len(rows), len(reviewed), len(vetoed), len(reduced), len(vetoed) / len(reviewed) if reviewed else 0.0,
        false_veto, all_exp, veto_exp, kept_exp, incremental, total_cost, per_review,
        incremental - total_cost * api_cost_to_r,
    )
