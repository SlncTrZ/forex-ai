#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fetch_scalping_dataset import (
    CONTEXT_TIMEFRAMES,
    DEFAULT_HISTORY_BARS,
    DEFAULT_START,
    DEFAULT_WARMUP_BY_TF,
    DEFAULT_WEEKS,
    STRATEGY_TIMEFRAMES,
    TIMEFRAME_SECONDS,
    _utc_midnight,
    default_output_root,
)
from forex_ai.config import load_runtime_config
from forex_ai.market.context_config import load_market_context_snapshot
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol_strict
from scalping_segments import (
    DEFAULT_SEGMENT_DAYS,
    fetch_segment,
    merge_segment_files,
    target_segments,
    warmup_spec,
)

UTC = timezone.utc
ALL_TIMEFRAMES = (*STRATEGY_TIMEFRAMES, *CONTEXT_TIMEFRAMES)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_time(row: dict[str, object]) -> int:
    return int(row["time"])


def _validate_final_range(
    rows: list[dict[str, object]],
    *,
    range_start: datetime,
    range_end: datetime,
    timeframe_seconds: int,
    warmup_bars: int,
) -> tuple[int, int]:
    start_ts = int(range_start.timestamp())
    end_ts = int(range_end.timestamp())
    before = [row for row in rows if _row_time(row) < start_ts]
    target = [row for row in rows if start_ts <= _row_time(row) < end_ts]
    if len(before) != warmup_bars:
        raise RuntimeError(f"FINAL_WARMUP_COUNT_MISMATCH:{len(before)}:{warmup_bars}")
    if not target:
        raise RuntimeError("FINAL_TARGET_EMPTY")
    first_gap = _row_time(target[0]) - start_ts
    last_gap = end_ts - _row_time(target[-1])
    # Forex can have weekend/holiday gaps. One calendar day plus one bar width
    # is tight enough to catch missing segments without rejecting Friday close.
    max_boundary_gap = 86400 + timeframe_seconds
    if first_gap > max_boundary_gap:
        raise RuntimeError(f"FINAL_START_GAP_TOO_LARGE:{first_gap}")
    if last_gap > max_boundary_gap:
        raise RuntimeError(f"FINAL_END_GAP_TOO_LARGE:{last_gap}")
    timestamps = [_row_time(row) for row in rows]
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise RuntimeError("FINAL_ROWS_NOT_STRICTLY_ORDERED")
    return len(before), len(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch one raw symbol/timeframe slice using resumable historical segments.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True, choices=ALL_TIMEFRAMES)
    parser.add_argument("--week-start", default=DEFAULT_START.isoformat())
    parser.add_argument("--weeks", type=int, default=DEFAULT_WEEKS)
    parser.add_argument("--history-bars", type=int, default=DEFAULT_HISTORY_BARS)
    parser.add_argument("--output-root", default=str(default_output_root()))
    parser.add_argument("--segment-days", type=int, help="Override default target segment size for this timeframe.")
    parser.add_argument("--overwrite", action="store_true", help="Refetch segment checkpoints and replace final raw file.")
    args = parser.parse_args()

    if not args.symbol or len(args.symbol) > 64 or any(ch.isspace() for ch in args.symbol):
        raise ValueError(f"Invalid symbol: {args.symbol!r}")
    start_day = date.fromisoformat(args.week_start)
    if start_day.weekday() != 0:
        raise ValueError("week-start must be a Monday")
    if args.weeks <= 0 or args.history_bars < 50:
        raise ValueError("invalid raw fetch arguments")
    segment_days = args.segment_days or int(DEFAULT_SEGMENT_DAYS[args.timeframe])
    if segment_days <= 0:
        raise ValueError("segment-days must be positive")

    range_start = _utc_midnight(start_day)
    range_end = range_start + timedelta(days=7 * (args.weeks - 1) + 5)
    end_day = start_day + timedelta(days=7 * (args.weeks - 1) + 4)
    label = f"{start_day.isoformat()}_{end_day.isoformat()}"
    output_dir = Path(args.output_root).expanduser() / "scalping" / "data" / label

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        raise RuntimeError("MT5_CONNECT_FAILED")
    try:
        constants = client.constants()
        available = client.symbol_candidates((args.symbol,))
        actual = resolve_symbol_strict(args.symbol, available)
        if actual is None:
            raise RuntimeError(f"SYMBOL_MAPPING_UNRESOLVED:{args.symbol}")
        context = load_market_context_snapshot()
        warmup_bars = max(
            DEFAULT_WARMUP_BY_TF[args.timeframe],
            context.higher_timeframe_structure.timeframes[args.timeframe].history_bars
            if args.timeframe in CONTEXT_TIMEFRAMES else args.history_bars,
        )
        timeframe = constants[args.timeframe]
        timeframe_seconds = TIMEFRAME_SECONDS[args.timeframe]
        segment_root = output_dir / actual / ".segments" / args.timeframe
        fetch_range = lambda start_ts, end_ts: client.bars_range(actual, timeframe, start_ts, end_ts)

        warmup_result = None
        for expansion in range(1, 5):
            spec = warmup_spec(
                args.timeframe,
                timeframe_seconds=timeframe_seconds,
                warmup_bars=warmup_bars,
                range_start=range_start,
                expansion=expansion,
            )
            result = fetch_segment(
                fetch_range=fetch_range,
                spec=spec,
                segment_path=segment_root / f"{spec.key}.json",
                overwrite=args.overwrite,
            )
            print(json.dumps({
                "stage": "warmup",
                "segment": spec.key,
                "rows": result.row_count,
                "cache_hit": result.cache_hit,
            }, sort_keys=True))
            if result.row_count >= warmup_bars:
                warmup_result = result
                break
        if warmup_result is None:
            raise RuntimeError(f"WARMUP_SEGMENTS_INSUFFICIENT:{actual}:{args.timeframe}:need={warmup_bars}")

        target_results = []
        for spec in target_segments(range_start, range_end, days=segment_days):
            result = fetch_segment(
                fetch_range=fetch_range,
                spec=spec,
                segment_path=segment_root / f"{spec.key}.json",
                overwrite=args.overwrite,
            )
            target_results.append(result)
            print(json.dumps({
                "stage": "target",
                "segment": spec.key,
                "rows": result.row_count,
                "cache_hit": result.cache_hit,
            }, sort_keys=True))
    finally:
        client.close()

    final_path = output_dir / actual / f"{args.timeframe}.json"
    if final_path.exists() and not args.overwrite:
        # Rebuilding from verified checkpoints is deterministic and safe; do not
        # require users to delete a prior final file when retrying missing segments.
        final_path.unlink()
    rows = merge_segment_files(
        segment_results=[warmup_result, *target_results],
        output_path=final_path,
        range_start=range_start,
        range_end=range_end,
        warmup_bars=warmup_bars,
    )
    before_count, target_count = _validate_final_range(
        rows,
        range_start=range_start,
        range_end=range_end,
        timeframe_seconds=timeframe_seconds,
        warmup_bars=warmup_bars,
    )
    print(json.dumps({
        "status": "complete",
        "base_symbol": args.symbol,
        "actual_symbol": actual,
        "timeframe": args.timeframe,
        "segment_days": segment_days,
        "warmup_rows": before_count,
        "target_rows": target_count,
        "rows_total": len(rows),
        "path": str(final_path),
        "sha256": _sha256(final_path),
        "first_time_utc": datetime.fromtimestamp(_row_time(rows[0]), UTC).isoformat(),
        "last_time_utc": datetime.fromtimestamp(_row_time(rows[-1]), UTC).isoformat(),
        "segments": 1 + len(target_results),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
