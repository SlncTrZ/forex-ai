#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from forex_ai.research.scalping_experiment_report import flatten_strategy_summaries


def _load_index(path: Path) -> dict[str, Any]:
    target = path / "experiment_index.json" if path.is_dir() else path
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != "forex-ai-scalping-experiment-index-v1":
        raise ValueError(f"unsupported experiment index: {target}")
    data["_index_path"] = str(target)
    return data


def _load_report(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _status_rank(status: str) -> int:
    return {
        "POSITIVE_BOTH_PARTITIONS": 0,
        "POSITIVE_BOTH_PARTITIONS_LOW_MARGIN": 1,
        "POSITIVE_COMBINED_PARTITION_MIXED": 2,
        "PARTITION_FLIP": 3,
        "NEGATIVE_SAMPLE": 4,
    }.get(status, 9)


def _candidate_note(row: dict[str, Any]) -> str:
    status = row["status"]
    if status == "POSITIVE_BOTH_PARTITIONS":
        return "Strongest sample evidence; freeze definition and validate prospectively before promotion."
    if status == "POSITIVE_BOTH_PARTITIONS_LOW_MARGIN":
        return "Both partitions positive but margin is thin; research candidate only."
    if status == "POSITIVE_COMBINED_PARTITION_MIXED":
        return "Combined positive but partitions disagree; do not promote from this sample."
    if status == "PARTITION_FLIP":
        return "Partition sign flip; treat as unstable."
    return "Negative sample; reject or redesign before spending more tuning budget."


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a curated weekly scalping research review from selected experiment directories/index files."
    )
    parser.add_argument("--inputs", nargs="+", required=True, help="experiment directories or experiment_index.json files")
    parser.add_argument("--week-label", required=True, help="human label, e.g. 2026-W36")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    indexes = [_load_index(Path(value)) for value in args.inputs]
    fingerprints = {index.get("dataset_source_fingerprint") for index in indexes}
    if len(fingerprints) != 1:
        raise ValueError(f"weekly review requires one dataset fingerprint, got {sorted(fingerprints)}")

    rows: list[dict[str, Any]] = []
    for index in indexes:
        fixed = index.get("fixed_overrides") or {}
        portfolio = index.get("portfolio") or {}
        for run in index.get("runs") or []:
            report = _load_report(run["report"])
            for summary in flatten_strategy_summaries(report):
                rows.append({
                    "experiment": index.get("name"),
                    "index_path": index["_index_path"],
                    "run_id": run.get("run_id"),
                    "config_fingerprint": run.get("strategy_config_fingerprint"),
                    "fixed_overrides": fixed,
                    "matrix_values": run.get("matrix_values") or {},
                    "risk_per_trade_pct": portfolio.get("risk_per_trade_pct"),
                    "max_active_total": portfolio.get("max_active_total"),
                    **summary,
                })

    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (
            row.get("symbol"),
            row.get("strategy_id"),
            row.get("strategy_config_fingerprint"),
            row.get("risk_per_trade_pct"),
            row.get("max_active_total"),
        )
        previous = deduped.get(key)
        if previous is None:
            row["duplicate_sources"] = []
            deduped[key] = row
        else:
            previous.setdefault("duplicate_sources", []).append({
                "experiment": row.get("experiment"),
                "run_id": row.get("run_id"),
                "index_path": row.get("index_path"),
            })
    rows = list(deduped.values())
    rows.sort(
        key=lambda row: (
            _status_rank(row["status"]),
            -(row["expectancy_r"] if row["expectancy_r"] is not None else -999.0),
            row["strategy_id"],
        )
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "weekly_candidates.csv"
    md_path = output_dir / "weekly_review.md"
    json_path = output_dir / "weekly_review.json"

    csv_fields = [
        "experiment", "run_id", "symbol", "strategy_id", "version", "status", "trades", "win_rate",
        "expectancy_r", "profit_factor", "max_drawdown_r", "oos_expectancy_r", "is_expectancy_r",
        "stop_rate", "target_rate", "risk_per_trade_pct", "max_active_total", "strategy_config_fingerprint", "config_fingerprint",
        "fixed_overrides", "matrix_values", "duplicate_sources", "index_path",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            out = {key: row.get(key) for key in csv_fields}
            out["fixed_overrides"] = json.dumps(out["fixed_overrides"], sort_keys=True)
            out["matrix_values"] = json.dumps(out["matrix_values"], sort_keys=True)
            out["duplicate_sources"] = json.dumps(out["duplicate_sources"], sort_keys=True)
            writer.writerow(out)

    payload = {
        "schema": "forex-ai-scalping-weekly-review-v1",
        "week_label": args.week_label,
        "dataset_source_fingerprint": next(iter(fingerprints)),
        "experiment_count": len(indexes),
        "candidate_rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        f"# Scalping Weekly Research Review — {args.week_label}\n",
        f"Dataset fingerprint: `{next(iter(fingerprints))}`",
        f"Selected experiments: `{len(indexes)}`",
        f"Unique candidate rows after strategy-fingerprint dedupe: `{len(rows)}`\n",
        "## Ranked Research Evidence\n",
        "| Rank | Strategy | Experiment / Run | Win | Exp R | PF | DD R | OOS | IS | Stop | Target | Status |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for idx, row in enumerate(rows, start=1):
        lines.append(
            f"| {idx} | {row['strategy_id']} | {row['experiment']} / {row['run_id']} | {_pct(row['win_rate'])} | {_fmt(row['expectancy_r'])} | {_fmt(row['profit_factor'],3)} | {_fmt(row['max_drawdown_r'],2)} | {_fmt(row['oos_expectancy_r'])} | {_fmt(row['is_expectancy_r'])} | {_pct(row['stop_rate'])} | {_pct(row['target_rate'])} | {row['status']} |"
        )

    lines.append("\n## Candidate Notes\n")
    for idx, row in enumerate(rows, start=1):
        overrides = dict(row.get("fixed_overrides") or {})
        overrides.update(row.get("matrix_values") or {})
        lines.append(f"### {idx}. `{row['strategy_id']}` — {row['experiment']} / {row['run_id']}")
        lines.append(f"- Research status: **{row['status']}**")
        lines.append(f"- Note: {_candidate_note(row)}")
        lines.append(f"- Risk/trade: `{row.get('risk_per_trade_pct')}`; max active: `{row.get('max_active_total')}`")
        if overrides:
            lines.append("- Tested overrides:")
            for key, value in sorted(overrides.items()):
                lines.append(f"  - `{key}` = `{value}`")
        lines.append(f"- Strategy fingerprint: `{row.get('strategy_config_fingerprint')}`")
        lines.append(f"- Experiment config fingerprint: `{row['config_fingerprint']}`")
        if row.get("duplicate_sources"):
            lines.append(f"- Duplicate evidence sources collapsed: `{len(row['duplicate_sources'])}`")
        lines.append("")

    lines.append("## Next-Week Application Boundary\n")
    lines.append("- This review does not alter active config automatically.")
    lines.append("- Freeze any chosen candidate parameters before the next observation window begins.")
    lines.append("- Once a parameter is chosen from this eight-week sample, treat this sample as tuning/diagnostic data, not fresh OOS.")
    lines.append("- Apply selected research candidates to shadow/demo/prospective observation first; live promotion remains a separate safety decision.\n")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "status": "complete",
        "week_label": args.week_label,
        "experiment_count": len(indexes),
        "candidate_rows": len(rows),
        "review_md": str(md_path),
        "review_json": str(json_path),
        "candidates_csv": str(csv_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
