from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable, Mapping

from forex_ai.research.scalping_config import ScalpingResearchConfig, ScalpingStrategySpec
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
from forex_ai.strategy.v1.contracts import fingerprint


def _variant(spec: ScalpingStrategySpec, variant_id: str, **updates: Any) -> tuple[str, ScalpingStrategySpec]:
    parameters = spec.parameters.model_copy(update=updates)
    config_fingerprint = fingerprint({
        "strategy_id": spec.strategy_id,
        "version": spec.version,
        "parameters": parameters.model_dump(mode="python"),
    })
    return variant_id, ScalpingStrategySpec(
        strategy_id=spec.strategy_id,
        version=spec.version,
        enabled=True,
        parameters=parameters,
        fingerprint=config_fingerprint,
    )


def default_variants(spec: ScalpingStrategySpec) -> tuple[tuple[str, ScalpingStrategySpec], ...]:
    p = spec.parameters
    baseline = ("baseline", spec)
    if spec.strategy_id == "inside_bar_momentum_breakout_v1":
        return (
            baseline,
            _variant(spec, "range_0.70", mother_min_range_atr=0.70),
            _variant(spec, "range_1.10", mother_min_range_atr=1.10),
            _variant(spec, "body_0.45", mother_min_body_ratio=0.45),
            _variant(spec, "body_0.65", mother_min_body_ratio=0.65),
            _variant(spec, "target_1.00", target_r=1.00),
            _variant(spec, "target_1.50", target_r=1.50),
            _variant(spec, "expiry_30", expiry_minutes=30),
            _variant(spec, "expiry_60", expiry_minutes=60),
        )
    if spec.strategy_id == "ema_cross_scalp_v1":
        return (
            baseline,
            _variant(spec, "stop_lookback_2", stop_lookback_bars=2),
            _variant(spec, "stop_lookback_3", stop_lookback_bars=3),
            _variant(spec, "target_1.00", target_r=1.00),
            _variant(spec, "target_2.00", target_r=2.00),
            _variant(spec, "expiry_30", expiry_minutes=30),
            _variant(spec, "expiry_60", expiry_minutes=60),
        )
    if spec.strategy_id == "breakout_retest_v1":
        return (
            baseline,
            _variant(spec, "range_12", range_bars=12),
            _variant(spec, "range_30", range_bars=30),
            _variant(spec, "search_4", breakout_search_bars=4),
            _variant(spec, "search_10", breakout_search_bars=10),
            _variant(spec, "retest_0.10", retest_tolerance_atr=0.10),
            _variant(spec, "retest_0.30", retest_tolerance_atr=0.30),
            _variant(spec, "target_1.00", target_r=1.00),
            _variant(spec, "target_2.00", target_r=2.00),
            _variant(spec, "expiry_45", expiry_minutes=45),
            _variant(spec, "expiry_90", expiry_minutes=90),
        )
    if spec.strategy_id == "pinbar_reversal_v1":
        return (
            baseline,
            _variant(spec, "range_0.35", min_range_atr=0.35),
            _variant(spec, "range_0.75", min_range_atr=0.75),
            _variant(spec, "body_0.25", max_body_ratio=0.25),
            _variant(spec, "body_0.45", max_body_ratio=0.45),
            _variant(spec, "wick_0.50", min_primary_wick_ratio=0.50),
            _variant(spec, "wick_0.65", min_primary_wick_ratio=0.65),
            _variant(spec, "sr_0.75", max_sr_distance_atr=0.75),
            _variant(spec, "sr_2.50", max_sr_distance_atr=2.50),
            _variant(spec, "target_1.00", target_r=1.00),
            _variant(spec, "target_2.00", target_r=2.00),
        )
    raise KeyError(spec.strategy_id)


def _week_start_iso(timestamp: str) -> str:
    value = datetime.fromisoformat(timestamp)
    monday = value.date().fromordinal(value.date().toordinal() - value.weekday())
    return monday.isoformat()


def _weekly_metrics(records: Iterable[TradeRecord]) -> dict[str, dict[str, float | int | None]]:
    groups: dict[str, list[TradeRecord]] = defaultdict(list)
    for record in records:
        groups[_week_start_iso(record.entry_time_utc)].append(record)
    output: dict[str, dict[str, float | int | None]] = {}
    for week, group in sorted(groups.items()):
        values = [record.realized_r for record in group]
        output[week] = {
            "trades": len(group),
            "expectancy_r": sum(values) / len(values) if values else None,
            "total_r": sum(values),
            "positive_rate": sum(value > 0 for value in values) / len(values) if values else None,
        }
    return output


def _robustness_view(summary: Mapping[str, Any], weekly: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    regime_values = [
        value["expectancy_r"]
        for value in summary["by_regime"].values()
        if value["trades"] >= 10 and value["expectancy_r"] is not None
    ]
    side_values = [
        value["expectancy_r"]
        for value in summary["by_side"].values()
        if value["trades"] >= 10 and value["expectancy_r"] is not None
    ]
    week_values = [
        float(value["expectancy_r"])
        for value in weekly.values()
        if value["trades"] >= 3 and value["expectancy_r"] is not None
    ]
    return {
        "regime_floor_r": min(regime_values) if regime_values else None,
        "side_floor_r": min(side_values) if side_values else None,
        "positive_weeks": sum(value > 0 for value in week_values),
        "negative_weeks": sum(value < 0 for value in week_values),
        "weekly_floor_r": min(week_values) if week_values else None,
        "weekly_ceiling_r": max(week_values) if week_values else None,
    }


def tune_strategy_family(
    dataset: ScalpingDataset,
    config: ScalpingResearchConfig,
    *,
    base_symbol: str,
    strategy_id: str,
    variants: tuple[tuple[str, ScalpingStrategySpec], ...] | None = None,
    progress: callable | None = None,
) -> dict[str, Any]:
    base_spec = config.strategies[strategy_id]
    candidates = variants or default_variants(base_spec)
    harness = config.harness
    horizons = harness.horizons_minutes
    evaluator = EVALUATORS[strategy_id]

    active: dict[str, _ActiveTrade] = {}
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    stats: dict[tuple[str, str], SignalStats] = defaultdict(SignalStats)
    records: dict[str, list[TradeRecord]] = defaultdict(list)
    last_event = None
    last_partition: str | None = None
    event_count = 0

    for event in dataset.iter_events(base_symbol):
        event_count += 1
        raw_partition = event.snapshot.metadata.get("partition")
        partition = str(raw_partition) if raw_partition is not None else None

        if last_event is not None and last_partition in {"OOS", "IS"}:
            gap_minutes = (event.clock_utc - last_event.clock_utc).total_seconds() / 60.0
            if partition == last_partition and gap_minutes > harness.max_market_gap_minutes:
                for variant_id, trade in list(active.items()):
                    records[variant_id].append(_close_trade(
                        trade,
                        event=last_event,
                        exit_price=_executable_close(last_event, trade.signal.side),
                        exit_reason="MARKET_GAP",
                        horizons=horizons,
                    ))
                    del active[variant_id]

        if partition != last_partition:
            if last_partition in {"OOS", "IS"} and last_event is not None:
                for variant_id, trade in list(active.items()):
                    records[variant_id].append(_close_trade(
                        trade,
                        event=last_event,
                        exit_price=_executable_close(last_event, trade.signal.side),
                        exit_reason="PARTITION_END",
                        horizons=horizons,
                    ))
                    del active[variant_id]
            last_partition = partition

        if partition in {"OOS", "IS"}:
            for variant_id, trade in list(active.items()):
                closed = _update_trade(
                    trade,
                    event,
                    horizons=horizons,
                    intrabar_policy=harness.intrabar_policy,
                )
                if closed is not None:
                    records[variant_id].append(closed)
                    del active[variant_id]

            for variant_id, spec in candidates:
                signal = evaluator(event.snapshot, spec, event.clock_utc)
                if signal is None:
                    continue
                signal = add_common_features(signal, event.snapshot, harness)
                stat_key = (variant_id, partition)
                current_stats = stats[stat_key]
                current_stats.generated += 1
                seen_key = (variant_id, partition)
                if signal.setup_key in seen[seen_key]:
                    current_stats.duplicates += 1
                    continue
                seen[seen_key].add(signal.setup_key)
                current_stats.unique += 1
                if variant_id in active:
                    current_stats.blocked_active_position += 1
                    continue
                active[variant_id] = _ActiveTrade(
                    signal=signal,
                    partition=partition,
                    marks={horizon: None for horizon in horizons},
                )
                current_stats.accepted += 1

        last_event = event
        if progress is not None and event_count % 50 == 0:
            progress(f"{strategy_id}: processed {event_count} M5 events")

    if last_event is not None and last_partition in {"OOS", "IS"}:
        for variant_id, trade in list(active.items()):
            records[variant_id].append(_close_trade(
                trade,
                event=last_event,
                exit_price=_executable_close(last_event, trade.signal.side),
                exit_reason="DATASET_END",
                horizons=horizons,
            ))
            del active[variant_id]

    output: dict[str, Any] = {
        "schema": "forex-ai-scalping-tuning-family-v1",
        "dataset_source_fingerprint": dataset.dataset_source_fingerprint,
        "strategy_config_fingerprint": config.fingerprint,
        "symbol": base_symbol,
        "strategy_id": strategy_id,
        "variants": {},
    }
    baseline_summary = None
    for variant_id, spec in candidates:
        variant_records = records[variant_id]
        partition_records = {
            partition: [record for record in variant_records if record.partition == partition]
            for partition in ("OOS", "IS")
        }
        partition_summaries = {
            partition: _summary(partition_records[partition], stats[(variant_id, partition)], horizons)
            for partition in ("OOS", "IS")
        }
        combined_stats = SignalStats()
        for partition in ("OOS", "IS"):
            current = stats[(variant_id, partition)]
            combined_stats.generated += current.generated
            combined_stats.unique += current.unique
            combined_stats.duplicates += current.duplicates
            combined_stats.blocked_active_position += current.blocked_active_position
            combined_stats.accepted += current.accepted
        combined = _summary(variant_records, combined_stats, horizons)
        weekly = _weekly_metrics(variant_records)
        result = {
            "config_fingerprint": spec.fingerprint,
            "parameters": spec.parameters.model_dump(mode="python"),
            "partitions": partition_summaries,
            "combined": combined,
            "weekly": weekly,
            "robustness": _robustness_view(combined, weekly),
        }
        output["variants"][variant_id] = result
        if variant_id == "baseline":
            baseline_summary = result

    if baseline_summary is not None:
        base_oos = baseline_summary["partitions"]["OOS"]["expectancy_r"]
        base_is = baseline_summary["partitions"]["IS"]["expectancy_r"]
        base_combined = baseline_summary["combined"]["expectancy_r"]
        for variant_id, result in output["variants"].items():
            oos = result["partitions"]["OOS"]["expectancy_r"]
            ins = result["partitions"]["IS"]["expectancy_r"]
            combined = result["combined"]["expectancy_r"]
            result["dominates_baseline"] = bool(
                variant_id != "baseline"
                and oos is not None and ins is not None and combined is not None
                and base_oos is not None and base_is is not None and base_combined is not None
                and oos >= base_oos and ins >= base_is and combined > base_combined
            )
    return output
