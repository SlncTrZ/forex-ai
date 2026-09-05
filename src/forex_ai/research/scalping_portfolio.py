from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from forex_ai.research.replay import ReplayEvent
from forex_ai.research.scalping_config import ScalpingResearchConfig
from forex_ai.research.scalping_dataset import ScalpingDataset
from forex_ai.research.scalping_harness import (
    SignalStats,
    TradeRecord,
    _ActiveTrade,
    _close_trade,
    _executable_close,
    _summary,
    _update_trade,
)
from forex_ai.research.scalping_strategies import EVALUATORS, add_common_features


@dataclass
class _PortfolioActive:
    trade: _ActiveTrade
    risk_amount: float


ProgressCallback = Callable[[str], None]


def run_scalping_portfolio_harness(
    dataset: ScalpingDataset,
    config: ScalpingResearchConfig,
    *,
    symbols: tuple[str, ...],
    risk_per_trade_pct: float,
    max_active_total: int,
    initial_balance: float = 100.0,
    progress: ProgressCallback | None = None,
    progress_every: int = 250,
) -> tuple[dict[str, Any], list[TradeRecord]]:
    if not (0 < risk_per_trade_pct <= 100):
        raise ValueError("risk_per_trade_pct must be in (0, 100]")
    if max_active_total <= 0:
        raise ValueError("max_active_total must be positive")
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")

    horizons = config.harness.horizons_minutes
    all_records: list[TradeRecord] = []
    stats: dict[tuple[str, str, str], SignalStats] = defaultdict(SignalStats)
    account_by_symbol: dict[str, Any] = {}

    for base_symbol in symbols:
        active: dict[str, _PortfolioActive] = {}
        seen: dict[tuple[str, str], set[str]] = defaultdict(set)
        balance = float(initial_balance)
        peak_balance = balance
        max_drawdown_pct = 0.0
        max_active_seen = 0
        blocked_portfolio_limit = 0
        blocked_no_balance = 0
        pnl_by_strategy: dict[str, float] = defaultdict(float)
        risked_by_strategy: dict[str, float] = defaultdict(float)
        last_event: ReplayEvent | None = None
        last_partition: str | None = None
        event_count = 0

        def settle(record: TradeRecord, risk_amount: float) -> None:
            nonlocal balance, peak_balance, max_drawdown_pct
            pnl = record.realized_r * risk_amount
            balance += pnl
            pnl_by_strategy[record.strategy_id] += pnl
            risked_by_strategy[record.strategy_id] += risk_amount
            peak_balance = max(peak_balance, balance)
            if peak_balance > 0:
                max_drawdown_pct = max(max_drawdown_pct, (peak_balance - balance) / peak_balance * 100.0)
            all_records.append(record)

        def close_all(event: ReplayEvent, reason: str) -> None:
            for signal_id, item in list(active.items()):
                record = _close_trade(
                    item.trade,
                    event=event,
                    exit_price=_executable_close(event, item.trade.signal.side),
                    exit_reason=reason,
                    horizons=horizons,
                )
                settle(record, item.risk_amount)
                del active[signal_id]

        for event in dataset.iter_events(base_symbol):
            event_count += 1
            raw_partition = event.snapshot.metadata.get("partition")
            partition = str(raw_partition) if raw_partition is not None else None

            if last_event is not None and last_partition in {"OOS", "IS"}:
                gap_minutes = (event.clock_utc - last_event.clock_utc).total_seconds() / 60.0
                if partition == last_partition and gap_minutes > config.harness.max_market_gap_minutes:
                    # Close at the last executable quote before the observed market/session gap.
                    close_all(last_event, "MARKET_CLOSE")

            if partition != last_partition:
                if last_partition in {"OOS", "IS"} and last_event is not None:
                    close_all(last_event, "PARTITION_END")
                last_partition = partition

            if partition in {"OOS", "IS"}:
                for signal_id, item in list(active.items()):
                    closed = _update_trade(
                        item.trade,
                        event,
                        horizons=horizons,
                        intrabar_policy=config.harness.intrabar_policy,
                    )
                    if closed is not None:
                        settle(closed, item.risk_amount)
                        del active[signal_id]

                for spec in config.enabled_strategies():
                    evaluator = EVALUATORS[spec.strategy_id]
                    signal = evaluator(event.snapshot, spec, event.clock_utc)
                    if signal is None:
                        continue
                    signal = add_common_features(signal, event.snapshot, config.harness)
                    seen_key = (partition, spec.strategy_id)
                    stat_key = (base_symbol, partition, spec.strategy_id)
                    current_stats = stats[stat_key]
                    current_stats.generated += 1
                    if signal.setup_key in seen[seen_key]:
                        current_stats.duplicates += 1
                        continue
                    seen[seen_key].add(signal.setup_key)
                    current_stats.unique += 1
                    if balance <= 0:
                        blocked_no_balance += 1
                        continue
                    if len(active) >= max_active_total:
                        current_stats.blocked_active_position += 1
                        blocked_portfolio_limit += 1
                        continue
                    risk_amount = balance * risk_per_trade_pct / 100.0
                    active[signal.signal_id] = _PortfolioActive(
                        trade=_ActiveTrade(
                            signal=signal,
                            partition=partition,
                            marks={horizon: None for horizon in horizons},
                        ),
                        risk_amount=risk_amount,
                    )
                    current_stats.accepted += 1
                    max_active_seen = max(max_active_seen, len(active))

            last_event = event
            if progress is not None and progress_every > 0 and event_count % progress_every == 0:
                progress(f"{base_symbol}: processed {event_count} M5 events")

        if last_event is not None and last_partition in {"OOS", "IS"}:
            close_all(last_event, "DATASET_END")
        if progress is not None:
            progress(f"{base_symbol}: complete, events={event_count}")

        account_by_symbol[base_symbol] = {
            "initial_balance": initial_balance,
            "final_balance": balance,
            "return_pct": (balance / initial_balance - 1.0) * 100.0,
            "max_drawdown_pct_realized": max_drawdown_pct,
            "risk_per_trade_pct": risk_per_trade_pct,
            "max_active_total": max_active_total,
            "max_active_seen": max_active_seen,
            "max_nominal_open_risk_pct": risk_per_trade_pct * max_active_seen,
            "daily_loss_limit_enabled": False,
            "weekly_loss_limit_enabled": False,
            "blocked_portfolio_limit": blocked_portfolio_limit,
            "blocked_no_balance": blocked_no_balance,
            "pnl_by_strategy": dict(pnl_by_strategy),
            "risked_by_strategy": dict(risked_by_strategy),
        }

    grouped: dict[tuple[str, str, str], list[TradeRecord]] = defaultdict(list)
    for record in all_records:
        base = "EURUSD" if record.symbol.startswith("EURUSD") else "XAUUSD" if record.symbol.startswith("XAUUSD") else record.symbol
        grouped[(base, record.partition, record.strategy_id)].append(record)

    report: dict[str, Any] = {
        "schema": "forex-ai-scalping-portfolio-report-v1",
        "dataset_source_fingerprint": dataset.dataset_source_fingerprint,
        "dataset_builder_version": dataset.builder_version,
        "strategy_config_fingerprint": config.fingerprint,
        "portfolio": {
            "risk_per_trade_pct": risk_per_trade_pct,
            "max_active_total": max_active_total,
            "initial_balance": initial_balance,
            "daily_loss_limit_enabled": False,
            "weekly_loss_limit_enabled": False,
            "close_before_market_gap": True,
        },
        "accounts": account_by_symbol,
        "symbols": {},
    }
    for base_symbol in symbols:
        report["symbols"][base_symbol] = {}
        for spec in config.enabled_strategies():
            combined: list[TradeRecord] = []
            combined_stats = SignalStats()
            strategy_report: dict[str, Any] = {
                "version": spec.version,
                "config_fingerprint": spec.fingerprint,
                "partitions": {},
            }
            for partition in ("OOS", "IS"):
                key = (base_symbol, partition, spec.strategy_id)
                records = grouped.get(key, [])
                current_stats = stats[key]
                strategy_report["partitions"][partition] = _summary(records, current_stats, horizons)
                combined.extend(records)
                combined_stats.generated += current_stats.generated
                combined_stats.unique += current_stats.unique
                combined_stats.duplicates += current_stats.duplicates
                combined_stats.blocked_active_position += current_stats.blocked_active_position
                combined_stats.accepted += current_stats.accepted
            strategy_report["combined"] = _summary(combined, combined_stats, horizons)
            report["symbols"][base_symbol][spec.strategy_id] = strategy_report
    return report, all_records
