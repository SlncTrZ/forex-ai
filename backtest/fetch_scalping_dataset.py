#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence

from forex_ai.config import load_runtime_config
from forex_ai.market.context_config import load_market_context_snapshot
from forex_ai.market.structure import build_higher_timeframe_structure
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol_strict
from forex_ai.research.replay import ReplayEvent
from forex_ai.research.scalping_dataset import BUILDER_VERSION
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot, fingerprint

UTC = timezone.utc
SCHEMA = "forex-ai-scalping-source-v1"
POINTER_SCHEMA = "forex-ai-scalping-dataset-pointer-v1"
PROFILE = "scalping_v1"
DEFAULT_SYMBOLS = ("EURUSD", "XAUUSD")
STRATEGY_TIMEFRAMES = ("M5", "M15", "H1")
CONTEXT_TIMEFRAMES = ("H4", "D1")
TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
DEFAULT_START = date(2026, 7, 13)
DEFAULT_WEEKS = 8
DEFAULT_HISTORY_BARS = 120
DEFAULT_WARMUP_BY_TF = {"M5": 160, "M15": 160, "H1": 160, "H4": 120, "D1": 120}


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def default_output_root() -> Path:
    explicit = os.getenv("FOREX_AI_BACKTEST_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    runtime_root = Path(os.getenv("FOREX_AI_RUNTIME_ROOT", "~/apps/forex-ai")).expanduser()
    return runtime_root / "backtest"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _row_time(row: Mapping[str, object]) -> int:
    return int(row["time"])


def _candle(row: Mapping[str, object]) -> Candle:
    return Candle(
        time_utc=datetime.fromtimestamp(_row_time(row), UTC),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("tick_volume") or row.get("real_volume") or 0.0),
    )


def _closed_snapshot(
    rows: Sequence[Mapping[str, object]],
    starts: Sequence[int],
    *,
    timeframe: str,
    clock: datetime,
    keep: int,
) -> TimeframeSnapshot:
    cutoff_start = int(clock.timestamp()) - TIMEFRAME_SECONDS[timeframe]
    end = bisect_right(starts, cutoff_start)
    begin = max(0, end - keep)
    return TimeframeSnapshot(timeframe, tuple(_candle(row) for row in rows[begin:end]))


def build_scalping_replay_events(
    *,
    symbol: str,
    point: float,
    rows_by_tf: Mapping[str, Sequence[Mapping[str, object]]],
    history_bars: int,
    range_start: datetime,
    range_end: datetime,
) -> tuple[ReplayEvent, ...]:
    """Small-test compatibility helper.

    Production research uses `ScalpingDataset.iter_events()` so full M5 datasets
    stream one event at a time instead of materializing ~1 GB replay files.
    """
    if point <= 0:
        raise ValueError("point must be positive")
    if history_bars < 50:
        raise ValueError("history_bars must be >= 50")
    missing = tuple(name for name in (*STRATEGY_TIMEFRAMES, *CONTEXT_TIMEFRAMES) if name not in rows_by_tf)
    if missing:
        raise ValueError(f"missing scalping timeframes: {missing}")

    context_snapshot = load_market_context_snapshot()
    context_config = context_snapshot.higher_timeframe_structure
    starts_by_tf = {name: tuple(_row_time(row) for row in rows_by_tf[name]) for name in rows_by_tf}
    events: list[ReplayEvent] = []
    for row in rows_by_tf["M5"]:
        start = datetime.fromtimestamp(_row_time(row), UTC)
        clock = start + timedelta(seconds=TIMEFRAME_SECONDS["M5"])
        if not (range_start < clock <= range_end):
            continue
        strategy_tfs = {
            name: _closed_snapshot(rows_by_tf[name], starts_by_tf[name], timeframe=name, clock=clock, keep=history_bars)
            for name in STRATEGY_TIMEFRAMES
        }
        if min(len(tf.closed_bars) for tf in strategy_tfs.values()) < 50:
            continue
        context_tfs = {
            name: _closed_snapshot(
                rows_by_tf[name],
                starts_by_tf[name],
                timeframe=name,
                clock=clock,
                keep=context_config.timeframes[name].history_bars,
            )
            for name in CONTEXT_TIMEFRAMES
        }
        close = float(row["close"])
        spread_points = max(float(row.get("spread") or 0.0), 0.0)
        spread = spread_points * point
        bid = close
        ask = close + spread
        structure = build_higher_timeframe_structure(
            timeframes=context_tfs,
            reference_price=(bid + ask) / 2.0,
            captured_at_utc=clock,
            config=context_config,
            config_fingerprint=context_snapshot.fingerprint,
        )
        events.append(
            ReplayEvent(
                clock,
                MarketSnapshot(
                    symbol=symbol,
                    captured_at_utc=clock,
                    market_time_msc=int(clock.timestamp() * 1000),
                    bid=bid,
                    ask=ask,
                    timeframes=strategy_tfs,
                    spread_cost=spread,
                    commission_cost=0.0,
                    metadata={
                        "source": "mt5_broker_history",
                        "profile": PROFILE,
                        "anchor_timeframe": "M5",
                        "historical_spread_points": spread_points,
                    },
                    context={"higher_timeframe_structure": structure.to_dict()},
                ),
            )
        )
    return tuple(events)


def _segment_metadata(segment_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if not segment_root.is_dir():
        return rows
    for path in sorted(segment_root.glob("*.meta.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows.append({
            "path": str(path),
            "segment_key": payload.get("segment_key"),
            "rows": payload.get("rows"),
            "sha256": payload.get("sha256"),
            "start_utc": payload.get("start_utc"),
            "end_utc_exclusive": payload.get("end_utc_exclusive"),
        })
    return rows


def _validate_raw(
    path: Path,
    *,
    range_start: datetime,
    range_end: datetime,
    warmup_bars: int,
    timeframe_seconds: int,
) -> tuple[list[dict[str, object]], int, int]:
    if not path.is_file():
        raise FileNotFoundError(f"raw scalping cache missing: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"raw scalping cache must be a list: {path}")
    timestamps = [_row_time(row) for row in rows]
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError(f"raw scalping cache not strictly ordered: {path}")
    start_ts = int(range_start.timestamp())
    end_ts = int(range_end.timestamp())
    before = [row for row in rows if _row_time(row) < start_ts]
    target = [row for row in rows if start_ts <= _row_time(row) < end_ts]
    if len(before) < warmup_bars:
        raise ValueError(f"raw warmup insufficient: {path}:have={len(before)}:need={warmup_bars}")
    if not target:
        raise ValueError(f"raw target empty: {path}")
    max_gap = 86400 + timeframe_seconds
    if _row_time(target[0]) - start_ts > max_gap:
        raise ValueError(f"raw start boundary gap too large: {path}")
    if end_ts - _row_time(target[-1]) > max_gap:
        raise ValueError(f"raw end boundary gap too large: {path}")
    return rows, len(before), len(target)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finalize a compact frozen scalping dataset from segmented raw caches."
    )
    parser.add_argument("--week-start", default=DEFAULT_START.isoformat())
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output-root", default=str(default_output_root()))
    parser.add_argument("--history-bars", type=int, default=DEFAULT_HISTORY_BARS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reuse-raw", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.weeks <= 0 or args.history_bars < 50:
        raise ValueError("invalid scalping dataset arguments")
    invalid_symbols = tuple(symbol for symbol in args.symbols if not symbol or len(symbol) > 64 or any(ch.isspace() for ch in symbol))
    if invalid_symbols:
        raise ValueError(f"Invalid backtest symbols: {invalid_symbols}")
    start_day = date.fromisoformat(args.week_start)
    if start_day.weekday() != 0:
        raise ValueError("week-start must be a Monday")

    range_start = _utc_midnight(start_day)
    range_end = range_start + timedelta(days=7 * (args.weeks - 1) + 5)
    end_day = start_day + timedelta(days=7 * (args.weeks - 1) + 4)
    label = f"{start_day.isoformat()}_{end_day.isoformat()}"
    output_root = Path(args.output_root).expanduser()
    output_dir = output_root / "scalping" / "data" / label

    context_snapshot = load_market_context_snapshot()
    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        raise RuntimeError("MT5_CONNECT_FAILED")

    metadata: dict[str, object] = {
        "schema": SCHEMA,
        "profile": PROFILE,
        "source": "MT5 broker OHLC history; segmented raw cache; streaming replay",
        "completed_weeks": args.weeks,
        "range_start_utc": range_start.isoformat(),
        "range_end_utc_exclusive": range_end.isoformat(),
        "strategy_timeframes": list(STRATEGY_TIMEFRAMES),
        "context_timeframes": list(CONTEXT_TIMEFRAMES),
        "anchor_timeframe": "M5",
        "history_bars": args.history_bars,
        "builder_version": BUILDER_VERSION,
        "replay_materialized": False,
        "streaming_replay": True,
        "market_context_config_fingerprint": context_snapshot.fingerprint,
        "market_context_config": context_snapshot.higher_timeframe_structure.model_dump(mode="python"),
        "partitions": {
            "OOS": {"start_utc": "2026-07-13T00:00:00+00:00", "end_utc_exclusive": "2026-08-08T00:00:00+00:00"},
            "IS": {"start_utc": "2026-08-10T00:00:00+00:00", "end_utc_exclusive": "2026-09-05T00:00:00+00:00"},
        },
        "symbols": {},
    }
    try:
        available = client.symbol_candidates(tuple(args.symbols))
        for base in args.symbols:
            actual = resolve_symbol_strict(base, available)
            if actual is None:
                raise RuntimeError(f"SYMBOL_MAPPING_UNRESOLVED:{base}")
            info = client.symbol_info(actual)
            if not info or not info.get("point"):
                raise RuntimeError(f"SYMBOL_INFO_UNAVAILABLE:{actual}")
            timeframe_meta: dict[str, object] = {}
            for name in (*STRATEGY_TIMEFRAMES, *CONTEXT_TIMEFRAMES):
                path = output_dir / actual / f"{name}.json"
                warmup = max(
                    DEFAULT_WARMUP_BY_TF[name],
                    context_snapshot.higher_timeframe_structure.timeframes[name].history_bars
                    if name in CONTEXT_TIMEFRAMES else args.history_bars,
                )
                rows, before_count, target_count = _validate_raw(
                    path,
                    range_start=range_start,
                    range_end=range_end,
                    warmup_bars=warmup,
                    timeframe_seconds=TIMEFRAME_SECONDS[name],
                )
                timeframe_meta[name] = {
                    "path": str(path),
                    "rows_total": len(rows),
                    "rows_warmup": before_count,
                    "rows_target_range": target_count,
                    "first_time_utc": datetime.fromtimestamp(_row_time(rows[0]), UTC).isoformat(),
                    "last_time_utc": datetime.fromtimestamp(_row_time(rows[-1]), UTC).isoformat(),
                    "sha256": _sha256(path),
                    "segments": _segment_metadata(output_dir / actual / ".segments" / name),
                }
            metadata["symbols"][base] = {
                "actual_symbol": actual,
                "point": float(info["point"]),
                "timeframes": timeframe_meta,
            }
    finally:
        client.close()

    raw_identity = {
        "schema": SCHEMA,
        "builder_version": BUILDER_VERSION,
        "range_start_utc": range_start.isoformat(),
        "range_end_utc_exclusive": range_end.isoformat(),
        "history_bars": args.history_bars,
        "strategy_timeframes": list(STRATEGY_TIMEFRAMES),
        "context_timeframes": list(CONTEXT_TIMEFRAMES),
        "market_context_config_fingerprint": context_snapshot.fingerprint,
        "partitions": metadata["partitions"],
        "symbols": {
            base: {
                "actual_symbol": value["actual_symbol"],
                "point": value["point"],
                "raw_sha256": {
                    name: tf_meta["sha256"]
                    for name, tf_meta in value["timeframes"].items()
                },
            }
            for base, value in metadata["symbols"].items()
        },
    }
    metadata["dataset_source_fingerprint"] = fingerprint(raw_identity)

    source_manifest_path = output_dir / "source_manifest.json"
    pointer_path = output_root / "scalping" / "scalping_dataset.json"
    if not args.overwrite and (source_manifest_path.exists() or pointer_path.exists()):
        raise FileExistsError("scalping manifest/pointer exists; pass --overwrite")
    metadata["created_at_utc"] = datetime.now(UTC).isoformat()
    _write_json(source_manifest_path, metadata)
    pointer = {
        "schema": POINTER_SCHEMA,
        "dataset_root": str(output_dir),
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": _sha256(source_manifest_path),
        "builder_version": BUILDER_VERSION,
        "dataset_source_fingerprint": metadata["dataset_source_fingerprint"],
        "range_start_utc": range_start.isoformat(),
        "range_end_utc_exclusive": range_end.isoformat(),
        "symbols": list(args.symbols),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    _write_json(pointer_path, pointer)
    print(json.dumps({
        "status": "complete",
        "dataset_root": str(output_dir),
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": pointer["source_manifest_sha256"],
        "pointer": str(pointer_path),
        "symbols": list(args.symbols),
        "streaming_replay": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
