from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median
from typing import Any, Callable, Iterable, Mapping

from forex_ai.research.replay import ReplayEvent
from forex_ai.research.scalping_config import ScalpingResearchConfig, ScalpingStrategySpec
from forex_ai.research.scalping_dataset import ScalpingDataset
from forex_ai.research.scalping_strategies import EVALUATORS, ScalpingSignal, add_common_features


@dataclass
class _ActiveTrade:
    signal: ScalpingSignal
    partition: str
    mfe_r: float = 0.0
    mae_r: float = 0.0
    marks: dict[int, float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeRecord:
    signal_id: str
    setup_key: str
    strategy_id: str
    strategy_version: str
    strategy_config_fingerprint: str
    symbol: str
    partition: str
    side: str
    decision_timeframe: str
    entry_time_utc: str
    exit_time_utc: str
    entry: float
    stop_loss: float
    take_profit: float
    exit_price: float
    exit_reason: str
    realized_r: float
    mfe_r: float
    mae_r: float
    duration_minutes: float
    marks_r: Mapping[str, float | None]
    features: Mapping[str, Any]


@dataclass
class SignalStats:
    generated: int = 0
    unique: int = 0
    duplicates: int = 0
    blocked_active_position: int = 0
    accepted: int = 0


ProgressCallback = Callable[[str], None]


def _executable_close(event: ReplayEvent, side: str) -> float:
    return event.snapshot.bid if side == "BUY" else event.snapshot.ask


def _mark_r(signal: ScalpingSignal, price: float) -> float:
    signed = price - signal.entry if signal.side == "BUY" else signal.entry - price
    return signed / signal.risk


def _trade_extremes(event: ReplayEvent, side: str) -> tuple[float, float]:
    bar = event.snapshot.timeframes["M5"].closed_bars[-1]
    if side == "BUY":
        return bar.high, bar.low
    spread = event.snapshot.ask - event.snapshot.bid
    return bar.low + spread, bar.high + spread


def _close_trade(
    active: _ActiveTrade,
    *,
    event: ReplayEvent,
    exit_price: float,
    exit_reason: str,
    horizons: tuple[int, ...],
) -> TradeRecord:
    realized = _mark_r(active.signal, exit_price)
    for horizon in horizons:
        if active.marks.get(horizon) is None:
            active.marks[horizon] = realized
    return TradeRecord(
        signal_id=active.signal.signal_id,
        setup_key=active.signal.setup_key,
        strategy_id=active.signal.strategy_id,
        strategy_version=active.signal.strategy_version,
        strategy_config_fingerprint=active.signal.strategy_config_fingerprint,
        symbol=active.signal.symbol,
        partition=active.partition,
        side=active.signal.side,
        decision_timeframe=active.signal.decision_timeframe,
        entry_time_utc=active.signal.generated_at_utc.isoformat(),
        exit_time_utc=event.clock_utc.isoformat(),
        entry=active.signal.entry,
        stop_loss=active.signal.stop_loss,
        take_profit=active.signal.take_profit,
        exit_price=exit_price,
        exit_reason=exit_reason,
        realized_r=realized,
        mfe_r=active.mfe_r,
        mae_r=active.mae_r,
        duration_minutes=(event.clock_utc - active.signal.generated_at_utc).total_seconds() / 60.0,
        marks_r={str(horizon): active.marks[horizon] for horizon in horizons},
        features=dict(active.signal.features),
    )


def _update_trade(
    active: _ActiveTrade,
    event: ReplayEvent,
    *,
    horizons: tuple[int, ...],
    intrabar_policy: str,
) -> TradeRecord | None:
    if event.clock_utc <= active.signal.generated_at_utc:
        return None
    favorable_price, adverse_price = _trade_extremes(event, active.signal.side)
    if active.signal.side == "BUY":
        favorable_r = (favorable_price - active.signal.entry) / active.signal.risk
        adverse_r = (active.signal.entry - adverse_price) / active.signal.risk
        target_touch = favorable_price >= active.signal.take_profit
        stop_touch = adverse_price <= active.signal.stop_loss
    else:
        favorable_r = (active.signal.entry - favorable_price) / active.signal.risk
        adverse_r = (adverse_price - active.signal.entry) / active.signal.risk
        target_touch = favorable_price <= active.signal.take_profit
        stop_touch = adverse_price >= active.signal.stop_loss
    active.mfe_r = max(active.mfe_r, favorable_r, 0.0)
    active.mae_r = max(active.mae_r, adverse_r, 0.0)

    if target_touch and stop_touch:
        if intrabar_policy == "stop_first":
            return _close_trade(
                active,
                event=event,
                exit_price=active.signal.stop_loss,
                exit_reason="AMBIGUOUS_STOP_FIRST",
                horizons=horizons,
            )
        return _close_trade(
            active,
            event=event,
            exit_price=active.signal.take_profit,
            exit_reason="AMBIGUOUS_TARGET_FIRST",
            horizons=horizons,
        )
    if stop_touch:
        return _close_trade(
            active,
            event=event,
            exit_price=active.signal.stop_loss,
            exit_reason="STOP",
            horizons=horizons,
        )
    if target_touch:
        return _close_trade(
            active,
            event=event,
            exit_price=active.signal.take_profit,
            exit_reason="TARGET",
            horizons=horizons,
        )

    current_close = _executable_close(event, active.signal.side)
    for horizon in horizons:
        if active.marks.get(horizon) is None and event.clock_utc >= active.signal.generated_at_utc + timedelta(minutes=horizon):
            active.marks[horizon] = _mark_r(active.signal, current_close)
    if event.clock_utc >= active.signal.expires_at_utc:
        return _close_trade(
            active,
            event=event,
            exit_price=current_close,
            exit_reason="EXPIRY",
            horizons=horizons,
        )
    return None


def _max_drawdown(values: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _cohort_metrics(records: list[TradeRecord]) -> dict[str, Any]:
    values = [record.realized_r for record in records]
    positive = [value for value in values if value > 0]
    negative = [value for value in values if value < 0]
    return {
        "trades": len(records),
        "expectancy_r": mean(values) if values else None,
        "total_r": sum(values),
        "win_rate": len(positive) / len(values) if values else None,
        "profit_factor": sum(positive) / abs(sum(negative)) if negative else None,
        "max_drawdown_r": _max_drawdown(values),
        "mean_mfe_r": mean(record.mfe_r for record in records) if records else None,
        "mean_mae_r": mean(record.mae_r for record in records) if records else None,
    }


def _summary(records: list[TradeRecord], stats: SignalStats, horizons: tuple[int, ...]) -> dict[str, Any]:
    results = [record.realized_r for record in records]
    positives = [value for value in results if value > 0]
    negatives = [value for value in results if value < 0]
    summary: dict[str, Any] = {
        "signals_generated": stats.generated,
        "signals_unique": stats.unique,
        "signals_duplicates": stats.duplicates,
        "signals_blocked_active_position": stats.blocked_active_position,
        "signals_accepted": stats.accepted,
        "trades": len(records),
        "expectancy_r": mean(results) if results else None,
        "median_r": median(results) if results else None,
        "total_r": sum(results),
        "win_rate": len(positives) / len(results) if results else None,
        "loss_rate": len(negatives) / len(results) if results else None,
        "profit_factor": (
            sum(positives) / abs(sum(negatives))
            if negatives
            else None
        ),
        "max_drawdown_r": _max_drawdown(results),
        "mean_mfe_r": mean(record.mfe_r for record in records) if records else None,
        "mean_mae_r": mean(record.mae_r for record in records) if records else None,
        "exit_reasons": dict(Counter(record.exit_reason for record in records)),
        "horizons": {},
        "by_side": {},
        "by_regime": {},
        "by_regime_alignment": {},
    }
    for horizon in horizons:
        values = [record.marks_r[str(horizon)] for record in records if record.marks_r[str(horizon)] is not None]
        numeric = [float(value) for value in values if value is not None]
        summary["horizons"][str(horizon)] = {
            "count": len(numeric),
            "mean_mark_r": mean(numeric) if numeric else None,
            "positive_rate": sum(value > 0 for value in numeric) / len(numeric) if numeric else None,
        }
    for side in ("BUY", "SELL"):
        side_records = [record for record in records if record.side == side]
        summary["by_side"][side] = {
            **_cohort_metrics(side_records),
            "exit_reasons": dict(Counter(record.exit_reason for record in side_records)),
        }
    regime_values = ("UP", "DOWN", "SIDEWAYS", "UNAVAILABLE")
    for regime in regime_values:
        cohort = [record for record in records if record.features.get("regime") == regime]
        summary["by_regime"][regime] = _cohort_metrics(cohort)
    alignment_values = ("WITH_TREND", "COUNTER_TREND", "NO_TREND", "UNAVAILABLE")
    for alignment in alignment_values:
        cohort = [record for record in records if record.features.get("regime_alignment") == alignment]
        summary["by_regime_alignment"][alignment] = _cohort_metrics(cohort)
    return summary


def run_scalping_harness(
    dataset: ScalpingDataset,
    config: ScalpingResearchConfig,
    *,
    symbols: tuple[str, ...] = ("EURUSD", "XAUUSD"),
    progress: ProgressCallback | None = None,
    progress_every: int = 2000,
) -> tuple[dict[str, Any], list[TradeRecord]]:
    harness = config.harness
    horizons = harness.horizons_minutes
    all_records: list[TradeRecord] = []
    stats: dict[tuple[str, str, str], SignalStats] = defaultdict(SignalStats)

    for base_symbol in symbols:
        active: dict[str, _ActiveTrade] = {}
        seen: dict[tuple[str, str], set[str]] = defaultdict(set)
        last_event: ReplayEvent | None = None
        last_partition: str | None = None
        event_count = 0

        for event in dataset.iter_events(base_symbol):
            event_count += 1
            partition = event.snapshot.metadata.get("partition")
            partition = str(partition) if partition is not None else None

            if last_event is not None and last_partition in {"OOS", "IS"}:
                gap_minutes = (event.clock_utc - last_event.clock_utc).total_seconds() / 60.0
                if partition == last_partition and gap_minutes > harness.max_market_gap_minutes:
                    for strategy_id, trade in list(active.items()):
                        all_records.append(_close_trade(
                            trade,
                            event=last_event,
                            exit_price=_executable_close(last_event, trade.signal.side),
                            exit_reason="MARKET_GAP",
                            horizons=horizons,
                        ))
                        del active[strategy_id]

            if partition != last_partition:
                if last_partition in {"OOS", "IS"} and last_event is not None:
                    for strategy_id, trade in list(active.items()):
                        all_records.append(_close_trade(
                            trade,
                            event=last_event,
                            exit_price=_executable_close(last_event, trade.signal.side),
                            exit_reason="PARTITION_END",
                            horizons=horizons,
                        ))
                        del active[strategy_id]
                last_partition = partition

            if partition in {"OOS", "IS"}:
                for strategy_id, trade in list(active.items()):
                    closed = _update_trade(
                        trade,
                        event,
                        horizons=horizons,
                        intrabar_policy=harness.intrabar_policy,
                    )
                    if closed is not None:
                        all_records.append(closed)
                        del active[strategy_id]

                for spec in config.enabled_strategies():
                    evaluator = EVALUATORS[spec.strategy_id]
                    signal = evaluator(event.snapshot, spec, event.clock_utc)
                    if signal is None:
                        continue
                    signal = add_common_features(signal, event.snapshot, harness)
                    key = (partition, spec.strategy_id)
                    stat_key = (base_symbol, partition, spec.strategy_id)
                    current_stats = stats[stat_key]
                    current_stats.generated += 1
                    if signal.setup_key in seen[key]:
                        current_stats.duplicates += 1
                        continue
                    seen[key].add(signal.setup_key)
                    current_stats.unique += 1
                    if spec.strategy_id in active:
                        current_stats.blocked_active_position += 1
                        continue
                    active[spec.strategy_id] = _ActiveTrade(
                        signal=signal,
                        partition=partition,
                        marks={horizon: None for horizon in horizons},
                    )
                    current_stats.accepted += 1

            last_event = event
            if progress is not None and progress_every > 0 and event_count % progress_every == 0:
                progress(f"{base_symbol}: processed {event_count} M5 events")

        if last_event is not None and last_partition in {"OOS", "IS"}:
            for strategy_id, trade in list(active.items()):
                all_records.append(_close_trade(
                    trade,
                    event=last_event,
                    exit_price=_executable_close(last_event, trade.signal.side),
                    exit_reason="DATASET_END",
                    horizons=horizons,
                ))
                del active[strategy_id]
        if progress is not None:
            progress(f"{base_symbol}: complete, events={event_count}")

    grouped_records: dict[tuple[str, str, str], list[TradeRecord]] = defaultdict(list)
    for record in all_records:
        base = "EURUSD" if record.symbol.startswith("EURUSD") else "XAUUSD" if record.symbol.startswith("XAUUSD") else record.symbol
        grouped_records[(base, record.partition, record.strategy_id)].append(record)

    report: dict[str, Any] = {
        "schema": "forex-ai-scalping-batch-report-v1",
        "dataset_source_fingerprint": dataset.dataset_source_fingerprint,
        "dataset_builder_version": dataset.builder_version,
        "strategy_config_fingerprint": config.fingerprint,
        "strategy_config_path": str(config.source_path),
        "harness": config.harness.model_dump(mode="python"),
        "symbols": {},
    }
    for base_symbol in symbols:
        report["symbols"][base_symbol] = {}
        for spec in config.enabled_strategies():
            strategy_report: dict[str, Any] = {
                "version": spec.version,
                "config_fingerprint": spec.fingerprint,
                "partitions": {},
            }
            combined: list[TradeRecord] = []
            combined_stats = SignalStats()
            for partition in ("OOS", "IS"):
                key = (base_symbol, partition, spec.strategy_id)
                records = grouped_records.get(key, [])
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
