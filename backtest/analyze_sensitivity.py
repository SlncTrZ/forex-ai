#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from statistics import mean

from forex_ai.research.dataset import load_frozen_replay_dataset
from forex_ai.strategy.v1 import trend_pullback, volatility_breakout
from forex_ai.strategy.v1.contracts import CandidateEnvelope, StrategyConfig

SUPPORTED_BASES = ("EURUSD", "XAUUSD")
ACTUAL_BY_BASE = {"EURUSD": "EURUSDc", "XAUUSD": "XAUUSDc"}


@dataclass(frozen=True)
class SinglePositionResult:
    candidate_evals: int
    setup_clusters: int
    taken: int
    closed: int
    skipped_while_open: int
    unresolved_at_end: int
    expectancy_r: float
    total_r: float
    win_rate: float
    exits: dict[str, int]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_backtest_root() -> Path:
    explicit = os.getenv("FOREX_AI_BACKTEST_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    runtime_root = Path(os.getenv("FOREX_AI_RUNTIME_ROOT", "~/apps/forex-ai")).expanduser()
    return runtime_root / "backtest"


def _resolve_dataset_root(explicit: str | None) -> tuple[Path, dict[str, object] | None]:
    if explicit:
        return Path(explicit).expanduser(), None
    standard_path = _default_backtest_root() / "standard_dataset.json"
    if not standard_path.is_file():
        raise FileNotFoundError(f"standard dataset pointer is missing: {standard_path}")
    standard = json.loads(standard_path.read_text(encoding="utf-8"))
    if standard.get("schema") != "forex-ai-standard-backtest-v1":
        raise RuntimeError("UNSUPPORTED_STANDARD_DATASET_SCHEMA")
    source_manifest = Path(str(standard["source_manifest"])).expanduser()
    if not source_manifest.is_file() or _sha256(source_manifest) != standard.get("source_manifest_sha256"):
        raise RuntimeError("STANDARD_SOURCE_MANIFEST_HASH_MISMATCH")
    return Path(str(standard["dataset_root"])).expanduser(), standard


def _config(base: StrategyConfig, **updates: object) -> StrategyConfig:
    params = dict(base.parameters)
    params.update(updates)
    return StrategyConfig(base.version, params, base.instrument_class)


def _cluster_count(candidates: list[CandidateEnvelope], *, gap_minutes: int = 30) -> int:
    if not candidates:
        return 0
    clusters = 1
    previous = candidates[0]
    for current in candidates[1:]:
        same_side = current.side == previous.side
        close_in_time = current.generated_at_utc - previous.generated_at_utc <= timedelta(minutes=gap_minutes)
        if not (same_side and close_in_time):
            clusters += 1
        previous = current
    return clusters


def _single_position_replay(events, strategy, config: StrategyConfig) -> SinglePositionResult:
    open_trade: tuple[CandidateEnvelope, float, float] | None = None
    candidate_evals = 0
    skipped = 0
    candidates: list[CandidateEnvelope] = []
    rs: list[float] = []
    exits: Counter[str] = Counter()

    for event in events:
        latest = event.snapshot.timeframes["M15"].closed_bars[-1]
        if open_trade is not None:
            candidate, entry, risk = open_trade
            stop_hit = latest.low <= candidate.stop_loss if candidate.side == "BUY" else latest.high >= candidate.stop_loss
            target_hit = latest.high >= candidate.take_profit if candidate.side == "BUY" else latest.low <= candidate.take_profit
            expired = event.clock_utc >= candidate.expires_at_utc
            if stop_hit or target_hit or expired:
                if stop_hit:
                    exit_price, reason = candidate.stop_loss, "STOP"
                elif target_hit:
                    exit_price, reason = candidate.take_profit, "TARGET"
                else:
                    exit_price = event.snapshot.bid if candidate.side == "BUY" else event.snapshot.ask
                    reason = "EXPIRY"
                signed = exit_price - entry if candidate.side == "BUY" else entry - exit_price
                rs.append(signed / risk)
                exits[reason] += 1
                open_trade = None

        result = strategy(event.snapshot, config, event.clock_utc)
        candidate = result.candidate
        if candidate is None:
            continue
        candidate_evals += 1
        candidates.append(candidate)
        if open_trade is not None:
            skipped += 1
            continue
        risk = abs(candidate.reference_entry - candidate.stop_loss)
        if risk > 0:
            open_trade = (candidate, candidate.reference_entry, risk)

    unresolved = 1 if open_trade is not None else 0
    wins = sum(value > 0 for value in rs)
    return SinglePositionResult(
        candidate_evals=candidate_evals,
        setup_clusters=_cluster_count(candidates),
        taken=len(rs) + unresolved,
        closed=len(rs),
        skipped_while_open=skipped,
        unresolved_at_end=unresolved,
        expectancy_r=mean(rs) if rs else 0.0,
        total_r=sum(rs),
        win_rate=wins / len(rs) if rs else 0.0,
        exits=dict(exits),
    )


def _cluster_lifecycle_replay(events, strategy, config: StrategyConfig, gap_minutes: int = 30) -> SinglePositionResult:
    open_trade: tuple[CandidateEnvelope, float, float] | None = None
    previous_candidate: CandidateEnvelope | None = None
    candidate_evals = 0
    clusters = 0
    skipped = 0
    rs: list[float] = []
    exits: Counter[str] = Counter()

    for event in events:
        latest = event.snapshot.timeframes["M15"].closed_bars[-1]
        if open_trade is not None:
            candidate, entry, risk = open_trade
            stop_hit = latest.low <= candidate.stop_loss if candidate.side == "BUY" else latest.high >= candidate.stop_loss
            target_hit = latest.high >= candidate.take_profit if candidate.side == "BUY" else latest.low <= candidate.take_profit
            expired = event.clock_utc >= candidate.expires_at_utc
            if stop_hit or target_hit or expired:
                if stop_hit:
                    exit_price, reason = candidate.stop_loss, "STOP"
                elif target_hit:
                    exit_price, reason = candidate.take_profit, "TARGET"
                else:
                    exit_price = event.snapshot.bid if candidate.side == "BUY" else event.snapshot.ask
                    reason = "EXPIRY"
                signed = exit_price - entry if candidate.side == "BUY" else entry - exit_price
                rs.append(signed / risk)
                exits[reason] += 1
                open_trade = None

        result = strategy(event.snapshot, config, event.clock_utc)
        candidate = result.candidate
        if candidate is None:
            continue
        candidate_evals += 1
        same_cluster = (
            previous_candidate is not None
            and candidate.side == previous_candidate.side
            and candidate.generated_at_utc - previous_candidate.generated_at_utc <= timedelta(minutes=gap_minutes)
        )
        previous_candidate = candidate
        if same_cluster:
            skipped += 1
            continue
        clusters += 1
        if open_trade is not None:
            skipped += 1
            continue
        risk = abs(candidate.reference_entry - candidate.stop_loss)
        if risk > 0:
            open_trade = (candidate, candidate.reference_entry, risk)

    unresolved = 1 if open_trade is not None else 0
    wins = sum(value > 0 for value in rs)
    return SinglePositionResult(
        candidate_evals=candidate_evals,
        setup_clusters=clusters,
        taken=len(rs) + unresolved,
        closed=len(rs),
        skipped_while_open=skipped,
        unresolved_at_end=unresolved,
        expectancy_r=mean(rs) if rs else 0.0,
        total_r=sum(rs),
        win_rate=wins / len(rs) if rs else 0.0,
        exits=dict(exits),
    )


def _rank(rows: list[dict[str, object]], *, min_closed: int = 5) -> list[dict[str, object]]:
    eligible = [row for row in rows if int(row["result"]["closed"]) >= min_closed]
    return sorted(
        eligible,
        key=lambda row: (float(row["result"]["expectancy_r"]), float(row["result"]["total_r"])),
        reverse=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V1 parameter sensitivity with one-position-per-symbol replay.")
    parser.add_argument("--dataset-root", help="Frozen dataset directory. Defaults to runtime standard_dataset.json.")
    parser.add_argument("--output", help="Report JSON path. Defaults to <dataset-root>/sensitivity_report.json.")
    parser.add_argument("--symbols", nargs="+", default=list(SUPPORTED_BASES))
    args = parser.parse_args()

    unsupported = tuple(symbol for symbol in args.symbols if symbol not in SUPPORTED_BASES)
    if unsupported:
        raise ValueError(f"Unsupported sensitivity symbols: {unsupported}; allowed={SUPPORTED_BASES}")

    dataset_root, standard = _resolve_dataset_root(args.dataset_root)
    report: dict[str, object] = {
        "schema": "forex-ai-sensitivity-v2",
        "dataset_root": str(dataset_root),
        "standard_dataset": standard,
        "policy": {
            "symbols": list(args.symbols),
            "max_open_positions_per_symbol": 1,
            "candidate_cluster_gap_minutes": 30,
            "note": "Counterfactual research only; no strategy parameters are changed by this report.",
        },
        "symbols": {},
    }

    for base in args.symbols:
        actual = ACTUAL_BY_BASE[base]
        dataset = load_frozen_replay_dataset(dataset_root / actual / "replay.jsonl")
        events = dataset.events

        trend_rows: list[dict[str, object]] = []
        for expiry in (30, 45, 60, 90, 120):
            cfg = _config(trend_pullback.DEFAULT_CONFIG, expiry_minutes=expiry)
            result = _single_position_replay(events, trend_pullback.evaluate, cfg)
            lifecycle = _cluster_lifecycle_replay(events, trend_pullback.evaluate, cfg)
            trend_rows.append({"parameters": {"expiry_minutes": expiry}, "result": asdict(result), "cluster_lifecycle": asdict(lifecycle)})

        breakout_rows: list[dict[str, object]] = []
        for efficiency in (0.25, 0.275, 0.30, 0.325):
            for expansion in (1.10, 1.15, 1.20, 1.25):
                for expiry in (30, 45, 60, 90):
                    cfg = _config(
                        volatility_breakout.DEFAULT_CONFIG,
                        min_efficiency=efficiency,
                        min_expansion=expansion,
                        expiry_minutes=expiry,
                    )
                    result = _single_position_replay(events, volatility_breakout.evaluate, cfg)
                    lifecycle = _cluster_lifecycle_replay(events, volatility_breakout.evaluate, cfg)
                    breakout_rows.append({
                        "parameters": {
                            "min_efficiency": efficiency,
                            "min_expansion": expansion,
                            "expiry_minutes": expiry,
                        },
                        "result": asdict(result),
                        "cluster_lifecycle": asdict(lifecycle),
                    })

        report["symbols"][base] = {
            "actual_symbol": actual,
            "dataset_records": dataset.manifest.record_count,
            "dataset_sha256": dataset.manifest.dataset_sha256,
            "trend_pullback": {
                "baseline": next(row for row in trend_rows if row["parameters"]["expiry_minutes"] == 45),
                "grid": trend_rows,
                "ranked": _rank(trend_rows),
            },
            "volatility_breakout": {
                "baseline": next(
                    row for row in breakout_rows
                    if row["parameters"] == {"min_efficiency": 0.30, "min_expansion": 1.20, "expiry_minutes": 30}
                ),
                "grid": breakout_rows,
                "ranked": _rank(breakout_rows),
            },
        }

    output = Path(args.output).expanduser() if args.output else dataset_root / "sensitivity_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"dataset={dataset_root}")
    print(f"output={output}")
    for base in args.symbols:
        symbol_report = report["symbols"][base]
        trend = symbol_report["trend_pullback"]
        breakout = symbol_report["volatility_breakout"]
        print(base)
        print(" trend_baseline", trend["baseline"])
        print(" trend_top3", trend["ranked"][:3])
        print(" breakout_baseline", breakout["baseline"])
        print(" breakout_top5", breakout["ranked"][:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
