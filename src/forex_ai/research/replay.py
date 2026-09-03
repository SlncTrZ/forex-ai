from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from forex_ai.strategy.v1.contracts import CandidateEnvelope, MarketSnapshot, StrategyConfig, StrategyResult, fingerprint

StrategyCallable = Callable[[MarketSnapshot, StrategyConfig, datetime], StrategyResult]


@dataclass(frozen=True)
class CostModel:
    spread: float = 0.0
    commission_per_trade: float = 0.0
    slippage: float = 0.0
    swap_per_day: float = 0.0
    reject_probability: float = 0.0

    @property
    def fingerprint(self) -> str:
        return fingerprint(vars(self))


@dataclass(frozen=True)
class ReplayEvent:
    clock_utc: datetime
    snapshot: MarketSnapshot


@dataclass(frozen=True)
class ReplayTrade:
    candidate_id: str
    strategy_id: str
    symbol: str
    side: str
    entry_time_utc: datetime
    exit_time_utc: datetime
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    gross_r: float
    net_r: float
    pnl_account_currency: float
    exit_reason: str
    total_cost: float


@dataclass(frozen=True)
class ReplayArtifact:
    dataset_fingerprint: str
    strategy_config_fingerprint: str
    cost_model_fingerprint: str
    trades: tuple[ReplayTrade, ...]
    candidate_count: int
    rejected_count: int


@dataclass
class _OpenTrade:
    candidate: CandidateEnvelope
    entry_time_utc: datetime
    entry_price: float
    risk: float
    accrued_swap: float = 0.0
    last_clock: datetime | None = None


class ReplayEngine:
    """Event-driven replay using the exact supplied V1 strategy callable.

    The engine observes only each event's frozen snapshot. It never imports MT5,
    runtime, journal, risk, execution, or LLM modules.
    """

    def __init__(self, strategy: StrategyCallable, config: StrategyConfig, cost_model: CostModel = CostModel()):
        self.strategy = strategy
        self.config = config
        self.cost_model = cost_model

    def _rejected(self, candidate_id: str) -> bool:
        probability = min(max(self.cost_model.reject_probability, 0.0), 1.0)
        if probability <= 0:
            return False
        value = int(hashlib.sha256(candidate_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return value < probability

    def run(self, events: Iterable[ReplayEvent], account_r_value: float = 1.0) -> ReplayArtifact:
        event_list = tuple(events)
        dataset_fp = fingerprint([{"clock": e.clock_utc, "snapshot": e.snapshot.fingerprint} for e in event_list])
        open_trades: dict[str, _OpenTrade] = {}
        seen_candidates: set[str] = set()
        trades: list[ReplayTrade] = []
        candidate_count = rejected_count = 0

        for event in event_list:
            latest_tf = event.snapshot.timeframes.get("M15")
            latest_bar = latest_tf.closed_bars[-1] if latest_tf and latest_tf.closed_bars else None
            if latest_bar:
                for candidate_id, opened in list(open_trades.items()):
                    candidate = opened.candidate
                    days = 0.0
                    if opened.last_clock is not None:
                        days = max((event.clock_utc - opened.last_clock).total_seconds(), 0.0) / 86400.0
                    opened.accrued_swap += self.cost_model.swap_per_day * days
                    opened.last_clock = event.clock_utc
                    if candidate.side == "BUY":
                        stop_hit = latest_bar.low <= candidate.stop_loss
                        target_hit = latest_bar.high >= candidate.take_profit
                    else:
                        stop_hit = latest_bar.high >= candidate.stop_loss
                        target_hit = latest_bar.low <= candidate.take_profit
                    if not (stop_hit or target_hit or event.clock_utc >= candidate.expires_at_utc):
                        continue
                    # Conservative same-bar ordering: stop wins when both are touched.
                    if stop_hit:
                        exit_price, reason = candidate.stop_loss, "STOP"
                    elif target_hit:
                        exit_price, reason = candidate.take_profit, "TARGET"
                    else:
                        exit_price, reason = (event.snapshot.bid if candidate.side == "BUY" else event.snapshot.ask), "EXPIRY"
                    signed_move = exit_price - opened.entry_price if candidate.side == "BUY" else opened.entry_price - exit_price
                    gross_r = signed_move / opened.risk
                    total_cost = self.cost_model.spread + 2 * self.cost_model.slippage + self.cost_model.commission_per_trade + opened.accrued_swap
                    net_r = gross_r - total_cost / opened.risk
                    trades.append(ReplayTrade(candidate_id, candidate.strategy_id, candidate.symbol, candidate.side,
                                              opened.entry_time_utc, event.clock_utc, opened.entry_price, exit_price,
                                              candidate.stop_loss, candidate.take_profit, gross_r, net_r,
                                              net_r * account_r_value, reason, total_cost))
                    del open_trades[candidate_id]

            result = self.strategy(event.snapshot, self.config, event.clock_utc)
            candidate = result.candidate
            if candidate is None or candidate.candidate_id in seen_candidates:
                continue
            seen_candidates.add(candidate.candidate_id)
            candidate_count += 1
            if self._rejected(candidate.candidate_id):
                rejected_count += 1
                continue
            risk = abs(candidate.reference_entry - candidate.stop_loss)
            if risk <= 0:
                rejected_count += 1
                continue
            entry_price = candidate.reference_entry + self.cost_model.slippage if candidate.side == "BUY" else candidate.reference_entry - self.cost_model.slippage
            open_trades[candidate.candidate_id] = _OpenTrade(candidate, event.clock_utc, entry_price, risk, 0.0, event.clock_utc)

        return ReplayArtifact(dataset_fp, self.config.fingerprint, self.cost_model.fingerprint, tuple(trades), candidate_count, rejected_count)
