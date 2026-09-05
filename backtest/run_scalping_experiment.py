#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import itertools
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from forex_ai.research.scalping_config import STRATEGY_IDS, load_scalping_research_config
from forex_ai.research.scalping_dataset import load_scalping_dataset
from forex_ai.research.scalping_experiment_report import render_experiment_summary, render_run_report
from forex_ai.research.scalping_harness import run_scalping_harness
from forex_ai.research.scalping_portfolio import run_scalping_portfolio_harness

UTC = timezone.utc
DEFAULT_DATASET = "/home/dinhtc/apps/forex-ai/backtest/scalping/scalping_dataset.json"
DEFAULT_BASE_CONFIG = "config/scalping-strategies.yaml"
DEFAULT_OUTPUT_ROOT = "/home/dinhtc/apps/forex-ai/backtest/scalping/results/experiments"
MAX_MATRIX_RUNS = 64


def _parse_scalar(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid scalar value: {text!r}") from exc


def _split_assignment(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise ValueError(f"expected KEY=VALUE, got {text!r}")
    key, value = text.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"empty override key in {text!r}")
    return key, value.strip()


def _set_dotted(root: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node: dict[str, Any] = root
    for part in parts[:-1]:
        current = node.get(part)
        if not isinstance(current, dict):
            raise ValueError(f"override path {dotted!r} does not resolve through mapping at {part!r}")
        node = current
    leaf = parts[-1]
    if leaf not in node:
        raise ValueError(f"override path {dotted!r} does not exist in base config")
    node[leaf] = value


def _parse_set(items: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for item in items:
        key, value = _split_assignment(item)
        output[key] = _parse_scalar(value)
    return output


def _parse_matrix(items: list[str]) -> dict[str, list[Any]]:
    output: dict[str, list[Any]] = {}
    for item in items:
        key, raw = _split_assignment(item)
        values = _parse_scalar(raw)
        if not isinstance(values, list) or not values:
            raise ValueError(
                f"matrix value for {key!r} must be a non-empty YAML list, e.g. {key}='[1.25, 1.5, 1.75]'"
            )
        output[key] = values
    return output


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return raw


def _apply_strategy_selection(raw: dict[str, Any], selected: tuple[str, ...] | None) -> None:
    if selected is None:
        return
    unknown = tuple(strategy_id for strategy_id in selected if strategy_id not in STRATEGY_IDS)
    if unknown:
        raise ValueError(f"unknown strategy ids: {unknown}")
    strategies = raw.get("strategies")
    if not isinstance(strategies, dict):
        raise ValueError("base config missing strategies mapping")
    chosen = set(selected)
    for strategy_id in STRATEGY_IDS:
        item = strategies.get(strategy_id)
        if not isinstance(item, dict):
            raise ValueError(f"base config missing strategy {strategy_id}")
        item["enabled"] = strategy_id in chosen


def _merge_experiment_file(
    *,
    experiment_path: Path | None,
    cli_name: str | None,
    cli_symbols: tuple[str, ...] | None,
    cli_strategies: tuple[str, ...] | None,
    cli_sets: dict[str, Any],
    cli_matrix: dict[str, list[Any]],
) -> tuple[str, tuple[str, ...] | None, tuple[str, ...] | None, dict[str, Any], dict[str, list[Any]]]:
    name = cli_name
    symbols = cli_symbols
    strategies = cli_strategies
    fixed = dict(cli_sets)
    matrix = dict(cli_matrix)
    if experiment_path is None:
        return name or "experiment", symbols, strategies, fixed, matrix

    raw = _load_yaml_mapping(experiment_path)
    allowed = {"name", "symbols", "strategies", "set", "matrix"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unsupported experiment keys: {sorted(unknown)}")
    if name is None and raw.get("name") is not None:
        name = str(raw["name"])
    if symbols is None and raw.get("symbols") is not None:
        if not isinstance(raw["symbols"], list) or not raw["symbols"]:
            raise ValueError("experiment symbols must be a non-empty list")
        symbols = tuple(str(item) for item in raw["symbols"])
    if strategies is None and raw.get("strategies") is not None:
        if not isinstance(raw["strategies"], list) or not raw["strategies"]:
            raise ValueError("experiment strategies must be a non-empty list")
        strategies = tuple(str(item) for item in raw["strategies"])

    file_set = raw.get("set") or {}
    file_matrix = raw.get("matrix") or {}
    if not isinstance(file_set, dict) or not isinstance(file_matrix, dict):
        raise ValueError("experiment set/matrix must be mappings")
    merged_set = {str(key): value for key, value in file_set.items()}
    merged_set.update(fixed)
    merged_matrix: dict[str, list[Any]] = {}
    for key, values in file_matrix.items():
        if not isinstance(values, list) or not values:
            raise ValueError(f"experiment matrix {key!r} must be a non-empty list")
        merged_matrix[str(key)] = values
    merged_matrix.update(matrix)
    return name or experiment_path.stem, symbols, strategies, merged_set, merged_matrix


def _safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("._-") or "experiment"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_trades_csv(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    horizons = sorted({key for record in records for key in record.marks_r}, key=int)
    feature_keys = sorted({key for record in records for key in record.features})
    base_fields = [
        "signal_id", "setup_key", "strategy_id", "strategy_version", "strategy_config_fingerprint",
        "symbol", "partition", "side", "decision_timeframe", "entry_time_utc", "exit_time_utc",
        "entry", "stop_loss", "take_profit", "exit_price", "exit_reason", "realized_r", "mfe_r",
        "mae_r", "duration_minutes",
    ]
    fieldnames = [*base_fields, *[f"mark_r_{value}" for value in horizons], *[f"feature_{key}" for key in feature_keys]]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            raw = asdict(record)
            row = {key: raw[key] for key in base_fields}
            row.update({f"mark_r_{value}": record.marks_r.get(value) for value in horizons})
            row.update({f"feature_{key}": record.features.get(key) for key in feature_keys})
            writer.writerow(row)


def _run_summary(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, strategies in report.get("symbols", {}).items():
        for strategy_id, value in strategies.items():
            combined = value["combined"]
            oos = value["partitions"]["OOS"]
            ins = value["partitions"]["IS"]
            rows.append({
                "symbol": symbol,
                "strategy_id": strategy_id,
                "version": value["version"],
                "trades": combined["trades"],
                "expectancy_r": combined["expectancy_r"],
                "total_r": combined["total_r"],
                "win_rate": combined["win_rate"],
                "profit_factor": combined["profit_factor"],
                "max_drawdown_r": combined["max_drawdown_r"],
                "oos_expectancy_r": oos["expectancy_r"],
                "is_expectancy_r": ins["expectancy_r"],
                "oos_win_rate": oos["win_rate"],
                "is_win_rate": ins["win_rate"],
            })
    return rows


def _matrix_combinations(matrix: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not matrix:
        return [{}]
    keys = list(matrix)
    combinations = [dict(zip(keys, values, strict=True)) for values in itertools.product(*(matrix[key] for key in keys))]
    if len(combinations) > MAX_MATRIX_RUNS:
        raise ValueError(
            f"matrix expands to {len(combinations)} runs; max is {MAX_MATRIX_RUNS}. "
            "Split the experiment to reduce accidental parameter mining."
        )
    return combinations


def _write_index_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Unified scalping experiment runner. Keep strategy code fixed while overriding validated config parameters "
            "from CLI or a YAML experiment file."
        )
    )
    parser.add_argument("--dataset-pointer", default=DEFAULT_DATASET)
    parser.add_argument("--base-config", default=DEFAULT_BASE_CONFIG)
    parser.add_argument("--experiment", help="optional YAML experiment definition")
    parser.add_argument("--name", help="experiment name")
    parser.add_argument("--symbols", nargs="+", help="dataset symbols to run, e.g. XAUUSD")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGY_IDS, help="enable only selected strategies")
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="fixed dotted config override; repeatable",
    )
    parser.add_argument(
        "--matrix",
        action="append",
        default=[],
        metavar="KEY='[V1,V2,...]'",
        help="dotted parameter matrix; repeatable; Cartesian product capped at 64 runs",
    )
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-trades-csv", action="store_true", help="skip per-trade CSV to save disk")
    parser.add_argument("--portfolio-risk-pct", type=float, help="enable portfolio replay with this account risk percent per trade")
    parser.add_argument("--max-active-total", type=int, default=3, help="global active-position cap for portfolio replay")
    parser.add_argument("--initial-balance", type=float, default=100.0, help="normalized starting balance for portfolio replay")
    args = parser.parse_args()

    dataset = load_scalping_dataset(Path(args.dataset_pointer))
    base_config_path = Path(args.base_config)
    base_raw = _load_yaml_mapping(base_config_path)

    name, symbols, strategies, fixed, matrix = _merge_experiment_file(
        experiment_path=Path(args.experiment) if args.experiment else None,
        cli_name=args.name,
        cli_symbols=tuple(args.symbols) if args.symbols else None,
        cli_strategies=tuple(args.strategies) if args.strategies else None,
        cli_sets=_parse_set(args.set),
        cli_matrix=_parse_matrix(args.matrix),
    )
    if symbols is None:
        symbols = tuple(dataset.manifest.get("symbols") or {})
    unknown_symbols = tuple(symbol for symbol in symbols if symbol not in (dataset.manifest.get("symbols") or {}))
    if unknown_symbols:
        raise ValueError(f"symbols not in dataset: {unknown_symbols}")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    experiment_dir = Path(args.output_root).expanduser() / f"{timestamp}_{_safe_name(name)}"
    experiment_dir.mkdir(parents=True, exist_ok=False)

    combinations = _matrix_combinations(matrix)
    index_rows: list[dict[str, Any]] = []
    index: dict[str, Any] = {
        "schema": "forex-ai-scalping-experiment-index-v1",
        "name": name,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset_pointer": str(Path(args.dataset_pointer)),
        "dataset_source_fingerprint": dataset.dataset_source_fingerprint,
        "dataset_builder_version": dataset.builder_version,
        "base_config": str(base_config_path),
        "symbols": list(symbols),
        "strategies": list(strategies) if strategies is not None else None,
        "fixed_overrides": fixed,
        "matrix": matrix,
        "portfolio": None if args.portfolio_risk_pct is None else {
            "risk_per_trade_pct": args.portfolio_risk_pct,
            "max_active_total": args.max_active_total,
            "initial_balance": args.initial_balance,
            "daily_loss_limit_enabled": False,
            "weekly_loss_limit_enabled": False,
        },
        "runs": [],
    }

    for run_number, matrix_values in enumerate(combinations, start=1):
        raw = json.loads(json.dumps(base_raw))
        _apply_strategy_selection(raw, strategies)
        for key, value in fixed.items():
            _set_dotted(raw, key, value)
        for key, value in matrix_values.items():
            _set_dotted(raw, key, value)

        run_id = f"run_{run_number:03d}"
        run_dir = experiment_dir / run_id
        run_dir.mkdir(parents=True)
        resolved_config = run_dir / "resolved_config.yaml"
        resolved_config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        config = load_scalping_research_config(resolved_config)

        print(
            json.dumps({
                "event": "run_start",
                "run_id": run_id,
                "matrix": matrix_values,
                "strategy_config_fingerprint": config.fingerprint,
            }, sort_keys=True),
            flush=True,
        )
        if args.portfolio_risk_pct is None:
            report, records = run_scalping_harness(
                dataset,
                config,
                symbols=symbols,
                progress=lambda message, rid=run_id: print(f"{rid}: {message}", flush=True),
                progress_every=250,
            )
        else:
            report, records = run_scalping_portfolio_harness(
                dataset,
                config,
                symbols=symbols,
                risk_per_trade_pct=args.portfolio_risk_pct,
                max_active_total=args.max_active_total,
                initial_balance=args.initial_balance,
                progress=lambda message, rid=run_id: print(f"{rid}: {message}", flush=True),
                progress_every=250,
            )
        report["generated_at_utc"] = datetime.now(UTC).isoformat()
        report["experiment_name"] = name
        report["run_id"] = run_id
        report["fixed_overrides"] = fixed
        report["matrix_values"] = matrix_values
        report["trades_total"] = len(records)

        report_path = run_dir / "report.json"
        _write_json(report_path, report)
        markdown_report_path = run_dir / "run_report.md"
        markdown_report_path.write_text(
            render_run_report(report, resolved_config_path=resolved_config),
            encoding="utf-8",
        )
        trades_path = run_dir / "trades.csv"
        if not args.no_trades_csv:
            _write_trades_csv(trades_path, records)

        summaries = _run_summary(report)
        for summary in summaries:
            row = {
                "run_id": run_id,
                "config_fingerprint": config.fingerprint,
                "portfolio_risk_pct": args.portfolio_risk_pct,
                "max_active_total": args.max_active_total if args.portfolio_risk_pct is not None else None,
                **{f"fixed:{key}": value for key, value in fixed.items()},
                **{f"matrix:{key}": value for key, value in matrix_values.items()},
                **summary,
            }
            index_rows.append(row)
        index["runs"].append({
            "run_id": run_id,
            "strategy_config_fingerprint": config.fingerprint,
            "matrix_values": matrix_values,
            "trades_total": len(records),
            "report": str(report_path),
            "run_report": str(markdown_report_path),
            "trades_csv": None if args.no_trades_csv else str(trades_path),
            "summary": summaries,
        })
        print(json.dumps({"event": "run_complete", "run_id": run_id, "trades": len(records)}, sort_keys=True), flush=True)

    index_path = experiment_dir / "experiment_index.json"
    summary_csv = experiment_dir / "summary.csv"
    summary_md = experiment_dir / "experiment_summary.md"
    _write_json(index_path, index)
    _write_index_csv(summary_csv, index_rows)
    summary_md.write_text(
        render_experiment_summary(
            name=name,
            dataset_fingerprint=dataset.dataset_source_fingerprint,
            run_entries=index["runs"],
        ),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "complete",
        "experiment_dir": str(experiment_dir),
        "runs": len(combinations),
        "index": str(index_path),
        "summary_csv": str(summary_csv),
        "summary_md": str(summary_md),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
