#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.research.scalping_config import STRATEGY_IDS, load_scalping_research_config
from forex_ai.research.scalping_dataset import load_scalping_dataset
from forex_ai.research.scalping_tuning import tune_strategy_family

UTC = timezone.utc


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one-factor-at-a-time XAU scalping strategy tuning on the standard 8-week dataset.")
    parser.add_argument(
        "--dataset-pointer",
        default="/home/dinhtc/apps/forex-ai/backtest/scalping/scalping_dataset.json",
    )
    parser.add_argument("--config", default="config/scalping-strategies.yaml")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--strategies", nargs="+", default=list(STRATEGY_IDS), choices=STRATEGY_IDS)
    parser.add_argument(
        "--output-dir",
        default="/home/dinhtc/apps/forex-ai/backtest/scalping/results/tuning_v1",
    )
    args = parser.parse_args()

    dataset = load_scalping_dataset(Path(args.dataset_pointer))
    config = load_scalping_research_config(Path(args.config))
    if args.symbol not in (dataset.manifest.get("symbols") or {}):
        raise ValueError(f"symbol not in standard scalping dataset: {args.symbol}")
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    index = {
        "schema": "forex-ai-scalping-tuning-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "symbol": args.symbol,
        "dataset_source_fingerprint": dataset.dataset_source_fingerprint,
        "strategy_config_fingerprint": config.fingerprint,
        "families": {},
    }
    for strategy_id in args.strategies:
        print(f"=== tuning {strategy_id} ===", flush=True)
        result = tune_strategy_family(
            dataset,
            config,
            base_symbol=args.symbol,
            strategy_id=strategy_id,
            progress=lambda message: print(message, flush=True),
        )
        path = output_dir / f"{strategy_id}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        index["families"][strategy_id] = {
            "path": str(path),
            "variants": len(result["variants"]),
            "dominates_baseline": [
                variant_id for variant_id, variant in result["variants"].items()
                if variant.get("dominates_baseline")
            ],
        }
        print(json.dumps(index["families"][strategy_id], sort_keys=True), flush=True)

    index_path = output_dir / "tuning_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"index={index_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
