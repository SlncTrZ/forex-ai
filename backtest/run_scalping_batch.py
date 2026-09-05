#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.research.scalping_config import load_scalping_research_config
from forex_ai.research.scalping_dataset import load_scalping_dataset
from forex_ai.research.scalping_harness import run_scalping_harness

UTC = timezone.utc


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the first common scalping research batch on scalping_v1.")
    parser.add_argument(
        "--dataset-pointer",
        default="/home/dinhtc/apps/forex-ai/backtest/scalping/scalping_dataset.json",
    )
    parser.add_argument("--config", default="config/scalping-strategies.yaml")
    parser.add_argument("--symbols", nargs="+", default=["EURUSD", "XAUUSD"])
    parser.add_argument(
        "--output-dir",
        default="/home/dinhtc/apps/forex-ai/backtest/scalping/results/batch1",
    )
    args = parser.parse_args()

    dataset = load_scalping_dataset(Path(args.dataset_pointer))
    config = load_scalping_research_config(Path(args.config))
    symbols = tuple(args.symbols)
    unknown = tuple(symbol for symbol in symbols if symbol not in (dataset.manifest.get("symbols") or {}))
    if unknown:
        raise ValueError(f"symbols not in scalping dataset: {unknown}")

    report, records = run_scalping_harness(
        dataset,
        config,
        symbols=symbols,
        progress=lambda message: print(message, flush=True),
    )
    report["generated_at_utc"] = datetime.now(UTC).isoformat()
    report["trades_total"] = len(records)
    output_dir = Path(args.output_dir).expanduser()
    report_path = output_dir / "batch1_report.json"
    trades_path = output_dir / "batch1_trades.csv"
    _write_json(report_path, report)
    _write_trades_csv(trades_path, records)

    print(json.dumps({
        "status": "complete",
        "dataset_source_fingerprint": dataset.dataset_source_fingerprint,
        "strategy_config_fingerprint": config.fingerprint,
        "trades": len(records),
        "report": str(report_path),
        "trades_csv": str(trades_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
