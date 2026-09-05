#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean
from typing import Callable

from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.config import load_strategy_snapshot
from forex_ai.strategy.v1 import exploration

HORIZONS = (15, 30, 45, 60, 90, 120)
ACTUAL = {"EURUSD": "EURUSDc", "XAUUSD": "XAUUSDc"}


def _backtest_root() -> Path:
    explicit = os.getenv("FOREX_AI_BACKTEST_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    runtime_root = Path(os.getenv("FOREX_AI_RUNTIME_ROOT", "~/apps/forex-ai")).expanduser()
    return runtime_root / "backtest"


def _default_output_root() -> Path:
    return _backtest_root() / "research" / "exploration_v1"


def _default_periods() -> dict[str, Path]:
    root = _backtest_root() / "data"
    return {
        "OOS": root / "2026-07-13_2026-08-07",
        "IS": root / "2026-08-10_2026-09-04",
    }


def _outcome(events, index, candidate, horizon_minutes: int) -> dict[str, object]:
    risk = abs(candidate.reference_entry - candidate.stop_loss)
    deadline = candidate.generated_at_utc + timedelta(minutes=horizon_minutes)
    last_snapshot = events[index].snapshot
    last_clock = events[index].clock_utc
    mfe = 0.0
    mae = 0.0
    first_touch = "NONE"

    for event in events[index + 1:]:
        if event.clock_utc > deadline:
            break
        bar = event.snapshot.timeframes["M15"].closed_bars[-1]
        last_snapshot = event.snapshot
        last_clock = event.clock_utc
        if candidate.side == "BUY":
            mfe = max(mfe, (bar.high - candidate.reference_entry) / risk)
            mae = max(mae, (candidate.reference_entry - bar.low) / risk)
            stop_hit = bar.low <= candidate.stop_loss
            target_hit = bar.high >= candidate.take_profit
        else:
            mfe = max(mfe, (candidate.reference_entry - bar.low) / risk)
            mae = max(mae, (bar.high - candidate.reference_entry) / risk)
            stop_hit = bar.high >= candidate.stop_loss
            target_hit = bar.low <= candidate.take_profit
        if first_touch == "NONE":
            if stop_hit and target_hit:
                first_touch = "AMBIGUOUS"
            elif stop_hit:
                first_touch = "SL"
            elif target_hit:
                first_touch = "TP"

    mark_price = last_snapshot.bid if candidate.side == "BUY" else last_snapshot.ask
    signed = mark_price - candidate.reference_entry if candidate.side == "BUY" else candidate.reference_entry - mark_price
    return {
        "complete": last_clock >= deadline,
        "mark_r": signed / risk,
        "mfe_r": mfe,
        "mae_r": mae,
        "first_touch": first_touch,
    }


def _family_records(period: str, symbol: str, events, family: str, evaluator: Callable) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    previous_candidate = None
    cluster_id = -1
    ordinal = 0
    for index, event in enumerate(events):
        result = evaluator(event.snapshot, now_utc=event.clock_utc)
        candidate = result.candidate
        if candidate is None:
            continue
        same_cluster = (
            previous_candidate is not None
            and candidate.side == previous_candidate.side
            and candidate.generated_at_utc - previous_candidate.generated_at_utc <= timedelta(minutes=30)
        )
        if same_cluster:
            ordinal += 1
        else:
            cluster_id += 1
            ordinal = 1
        previous_candidate = candidate
        evidence = dict(result.evidence.values)
        record: dict[str, object] = {
            "period": period,
            "symbol": symbol,
            "family": family,
            "candidate_id": candidate.candidate_id,
            "generated_at_utc": candidate.generated_at_utc.isoformat(),
            "side": candidate.side,
            "tier": evidence.get("tier"),
            "cluster_id": cluster_id,
            "cluster_ordinal": ordinal,
            "cluster_first": ordinal == 1,
            "failed_original_gates": list(evidence.get("failed_original_gates") or ()),
            "entry": candidate.reference_entry,
            "stop_loss": candidate.stop_loss,
            "take_profit": candidate.take_profit,
            "features": evidence,
            "outcomes": {},
        }
        for horizon in HORIZONS:
            record["outcomes"][str(horizon)] = _outcome(events, index, candidate, horizon)
        records.append(record)
    return records


def _aggregate(rows: list[dict[str, object]], horizon: int = 60) -> dict[str, object]:
    complete = [row for row in rows if bool(row["outcomes"][str(horizon)]["complete"])]
    if not complete:
        return {"count": len(rows), "complete": 0}
    outcomes = [row["outcomes"][str(horizon)] for row in complete]
    touches = Counter(str(item["first_touch"]) for item in outcomes)
    return {
        "count": len(rows),
        "complete": len(complete),
        "mean_mark_r": mean(float(item["mark_r"]) for item in outcomes),
        "mean_mfe_r": mean(float(item["mfe_r"]) for item in outcomes),
        "mean_mae_r": mean(float(item["mae_r"]) for item in outcomes),
        "positive_mark_rate": sum(float(item["mark_r"]) > 0 for item in outcomes) / len(outcomes),
        "mfe_ge_0_5_rate": sum(float(item["mfe_r"]) >= 0.5 for item in outcomes) / len(outcomes),
        "mfe_ge_1_rate": sum(float(item["mfe_r"]) >= 1.0 for item in outcomes) / len(outcomes),
        "mfe_ge_2_rate": sum(float(item["mfe_r"]) >= 2.0 for item in outcomes) / len(outcomes),
        "touches": dict(touches),
    }


def _summarize(records: list[dict[str, object]]) -> dict[str, object]:
    primary = [row for row in records if row["cluster_first"]]
    summary: dict[str, object] = {
        "all_candidates": len(records),
        "cluster_first_candidates": len(primary),
        "by_period_symbol_family_tier": {},
        "by_failed_gate_combo": {},
        "feature_slices": {},
    }

    groups: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    gate_groups: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in primary:
        key = (str(row["period"]), str(row["symbol"]), str(row["family"]), str(row["tier"]))
        groups[key].append(row)
        gate_combo = "+".join(row["failed_original_gates"]) if row["failed_original_gates"] else "NONE"
        gate_groups[(str(row["period"]), str(row["symbol"]), str(row["family"]), gate_combo)].append(row)

    for key, rows in sorted(groups.items()):
        summary["by_period_symbol_family_tier"]["|".join(key)] = {
            str(h): _aggregate(rows, h) for h in HORIZONS
        }
    for key, rows in sorted(gate_groups.items()):
        summary["by_failed_gate_combo"]["|".join(key)] = _aggregate(rows, 60)

    # A few deliberately coarse, interpretable slices. Continuous raw features stay in JSONL/CSV.
    for period in ("OOS", "IS"):
        for symbol in ACTUAL:
            trend_rows = [r for r in primary if r["period"] == period and r["symbol"] == symbol and r["family"] == "trend"]
            breakout_rows = [r for r in primary if r["period"] == period and r["symbol"] == symbol and r["family"] == "breakout"]
            for name, predicate in (
                ("trend_htf_aligned", lambda r: bool(r["features"].get("htf_aligned"))),
                ("trend_h1_mixed", lambda r: r["features"].get("h1") == "MIXED"),
                ("trend_reclaimed", lambda r: bool(r["features"].get("reclaimed"))),
                ("trend_not_reclaimed", lambda r: not bool(r["features"].get("reclaimed"))),
            ):
                rows = [r for r in trend_rows if predicate(r)]
                summary["feature_slices"][f"{period}|{symbol}|{name}"] = _aggregate(rows, 60)
            for confirmations in range(0, 5):
                rows = [r for r in breakout_rows if int(r["features"].get("confirmations", -1)) == confirmations]
                summary["feature_slices"][f"{period}|{symbol}|breakout_confirmations_{confirmations}"] = _aggregate(rows, 60)
    return summary


def _write_csv(path: Path, records: list[dict[str, object]]) -> None:
    fields = [
        "period", "symbol", "family", "candidate_id", "generated_at_utc", "side", "tier",
        "cluster_id", "cluster_ordinal", "cluster_first", "failed_original_gates",
        "mark_r_15", "mark_r_30", "mark_r_45", "mark_r_60", "mark_r_90", "mark_r_120",
        "mfe_r_60", "mae_r_60", "touch_60", "features_json",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in records:
            writer.writerow({
                "period": row["period"],
                "symbol": row["symbol"],
                "family": row["family"],
                "candidate_id": row["candidate_id"],
                "generated_at_utc": row["generated_at_utc"],
                "side": row["side"],
                "tier": row["tier"],
                "cluster_id": row["cluster_id"],
                "cluster_ordinal": row["cluster_ordinal"],
                "cluster_first": row["cluster_first"],
                "failed_original_gates": "+".join(row["failed_original_gates"]),
                **{f"mark_r_{h}": row["outcomes"][str(h)]["mark_r"] for h in HORIZONS},
                "mfe_r_60": row["outcomes"]["60"]["mfe_r"],
                "mae_r_60": row["outcomes"]["60"]["mae_r"],
                "touch_60": row["outcomes"]["60"]["first_touch"],
                "features_json": json.dumps(row["features"], ensure_ascii=False, sort_keys=True),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description="Run exploration_v1 across frozen research datasets.")
    parser.add_argument("--symbols", nargs="+", choices=tuple(ACTUAL), default=list(ACTUAL))
    parser.add_argument("--output-root", default=str(_default_output_root()))
    parser.add_argument("--period", action="append", default=[], help="LABEL=/absolute/dataset/root; may be repeated")
    args = parser.parse_args()

    periods = _default_periods()
    if args.period:
        periods = {}
        for item in args.period:
            label, raw_path = item.split("=", 1)
            periods[label] = Path(raw_path).expanduser()

    strategy_snapshot = load_strategy_snapshot()
    trend_config = strategy_snapshot.config_for("exploration_trend_v1")
    breakout_config = strategy_snapshot.config_for("exploration_breakout_v1")

    records: list[dict[str, object]] = []
    for period, root in periods.items():
        for base in args.symbols:
            dataset = load_frozen_replay_dataset(root / ACTUAL[base] / "replay.jsonl")
            records.extend(_family_records(
                period, base, dataset.events, "trend",
                lambda snapshot, now_utc: exploration.evaluate_trend(snapshot, trend_config, now_utc),
            ))
            records.extend(_family_records(
                period, base, dataset.events, "breakout",
                lambda snapshot, now_utc: exploration.evaluate_breakout(snapshot, breakout_config, now_utc),
            ))
            print(period, base, "events", dataset.manifest.record_count)

    output_root = Path(args.output_root).expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    tag = "_".join(args.symbols)
    jsonl_path = output_root / f"exploration_records_{tag}.jsonl"
    csv_path = output_root / f"exploration_records_{tag}.csv"
    summary_path = output_root / f"exploration_summary_{tag}.json"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    _write_csv(csv_path, records)
    summary = _summarize(records)
    summary["schema"] = "forex-ai-exploration-v1"
    summary["periods"] = {label: str(path) for label, path in periods.items()}
    summary["symbols"] = list(args.symbols)
    summary["horizons_minutes"] = list(HORIZONS)
    summary["strategy_config_fingerprint"] = strategy_snapshot.fingerprint
    summary["trend_config_fingerprint"] = trend_config.fingerprint
    summary["breakout_config_fingerprint"] = breakout_config.fingerprint
    summary["note"] = "Research-only. No live scanner or execution configuration is changed."
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("records=", len(records))
    print("jsonl=", jsonl_path)
    print("csv=", csv_path)
    print("summary=", summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
