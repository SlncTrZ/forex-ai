#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

try:
    from backtest.analyze_sensitivity import (
        ACTUAL_BY_BASE,
        SUPPORTED_BASES,
        _cluster_lifecycle_replay,
        _resolve_dataset_root,
        _single_position_replay,
    )
except ModuleNotFoundError:
    from analyze_sensitivity import (
        ACTUAL_BY_BASE,
        SUPPORTED_BASES,
        _cluster_lifecycle_replay,
        _resolve_dataset_root,
        _single_position_replay,
    )
from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.config import load_strategy_snapshot
from forex_ai.strategy.v1 import trend_pullback, volatility_breakout


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the canonical Forex-AI baseline backtest on production V1 defaults.")
    parser.add_argument("--dataset-root", help="Frozen dataset directory. Defaults to runtime standard_dataset.json.")
    parser.add_argument("--output", help="Output JSON. Defaults to <dataset-root>/standard_benchmark.json.")
    parser.add_argument("--symbols", nargs="+", default=list(SUPPORTED_BASES))
    args = parser.parse_args()

    unsupported = tuple(symbol for symbol in args.symbols if symbol not in SUPPORTED_BASES)
    if unsupported:
        raise ValueError(f"Unsupported standard backtest symbols: {unsupported}; allowed={SUPPORTED_BASES}")

    dataset_root, standard = _resolve_dataset_root(args.dataset_root)
    strategy_snapshot = load_strategy_snapshot()
    trend_config = strategy_snapshot.config_for("trend_pullback_v1")
    breakout_config = strategy_snapshot.config_for("volatility_breakout_v1")
    if standard is None:
        raise RuntimeError("STANDARD_BACKTEST_REQUIRES_CANONICAL_POINTER")

    report: dict[str, object] = {
        "schema": "forex-ai-standard-benchmark-v1",
        "dataset_root": str(dataset_root),
        "standard_dataset": standard,
        "policy": {
            "symbols": list(args.symbols),
            "strategy_parameters": "active-strategy-yaml",
            "strategy_config_fingerprint": strategy_snapshot.production_fingerprint,
            "max_open_positions_per_symbol": 1,
            "candidate_cluster_gap_minutes": 30,
            "note": "Canonical regression benchmark. No parameter optimization is performed.",
        },
        "symbols": {},
    }

    for base in args.symbols:
        actual = ACTUAL_BY_BASE[base]
        dataset = load_frozen_replay_dataset(dataset_root / actual / "replay.jsonl")
        events = dataset.events
        trend_single = _single_position_replay(events, trend_pullback.evaluate, trend_config)
        trend_cluster = _cluster_lifecycle_replay(events, trend_pullback.evaluate, trend_config)
        breakout_single = _single_position_replay(events, volatility_breakout.evaluate, breakout_config)
        breakout_cluster = _cluster_lifecycle_replay(events, volatility_breakout.evaluate, breakout_config)

        report["symbols"][base] = {
            "actual_symbol": actual,
            "dataset_records": dataset.manifest.record_count,
            "dataset_sha256": dataset.manifest.dataset_sha256,
            "trend_pullback": {
                "strategy_id": trend_config.version.strategy_id,
                "strategy_version": trend_config.version.version,
                "config_fingerprint": trend_config.fingerprint,
                "single_position": asdict(trend_single),
                "setup_lifecycle": asdict(trend_cluster),
            },
            "volatility_breakout": {
                "strategy_id": breakout_config.version.strategy_id,
                "strategy_version": breakout_config.version.version,
                "config_fingerprint": breakout_config.fingerprint,
                "single_position": asdict(breakout_single),
                "setup_lifecycle": asdict(breakout_cluster),
            },
        }

    output = Path(args.output).expanduser() if args.output else dataset_root / "standard_benchmark.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"dataset={dataset_root}")
    print(f"output={output}")
    for base, payload in report["symbols"].items():
        print(base)
        for strategy in ("trend_pullback", "volatility_breakout"):
            result = payload[strategy]
            print(strategy, "single", result["single_position"])
            print(strategy, "cluster", result["setup_lifecycle"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
