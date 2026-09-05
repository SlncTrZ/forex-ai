#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable

from forex_ai.config import ALLOWED_TRADING_SYMBOLS, load_runtime_config
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol_strict
from forex_ai.research.dataset import freeze_replay_dataset
from forex_ai.research.mt5_dataset import build_replay_events_from_mt5_bars

UTC = timezone.utc
TIMEFRAMES = ("M15", "H1", "H4")
DEFAULT_SYMBOLS = ("EURUSD", "XAUUSD")


def default_week_start(now_utc: datetime) -> date:
    now = now_utc.astimezone(UTC)
    monday = now.date() - timedelta(days=now.weekday())
    if now.weekday() < 5:
        monday -= timedelta(days=7)
    return monday


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _row_time(row: dict[str, object]) -> int:
    return int(row["time"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_window(
    client: MT5Client,
    *,
    symbol: str,
    timeframe: int,
    week_start: datetime,
    week_end: datetime,
    warmup_bars: int,
    chunk_size: int,
    max_chunks: int,
) -> list[dict[str, object]]:
    merged: dict[int, dict[str, object]] = {}
    start_ts = int(week_start.timestamp())
    end_ts = int(week_end.timestamp())

    for chunk_index in range(max_chunks):
        rows = client.bars(symbol, timeframe, chunk_size, start_pos=chunk_index * chunk_size)
        if not rows:
            break
        for row in rows:
            merged[_row_time(row)] = dict(row)
        ordered = [merged[key] for key in sorted(merged)]
        before = [row for row in ordered if _row_time(row) < start_ts]
        target = [row for row in ordered if start_ts <= _row_time(row) < end_ts]
        if len(before) >= warmup_bars and target:
            break
        if len(rows) < chunk_size:
            break
    else:
        raise RuntimeError(f"HISTORY_CHUNK_LIMIT:{symbol}:{timeframe}")

    ordered = [merged[key] for key in sorted(merged)]
    before = [row for row in ordered if _row_time(row) < start_ts]
    target = [row for row in ordered if start_ts <= _row_time(row) < end_ts]
    if len(before) < warmup_bars:
        raise RuntimeError(
            f"INSUFFICIENT_WARMUP:{symbol}:{timeframe}:have={len(before)}:need={warmup_bars}"
        )
    if not target:
        raise RuntimeError(f"NO_TARGET_WEEK_DATA:{symbol}:{timeframe}")
    return [*before[-warmup_bars:], *target]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def _remove_if_requested(paths: Iterable[Path], *, overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        raise FileExistsError("output exists; pass --overwrite: " + ", ".join(str(path) for path in existing))
    for path in existing:
        path.unlink()


def _default_output_root() -> Path:
    explicit = os.getenv("FOREX_AI_BACKTEST_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    runtime_root = Path(os.getenv("FOREX_AI_RUNTIME_ROOT", "~/apps/forex-ai")).expanduser()
    return runtime_root / "backtest"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the most recent completed Forex week from MT5 for offline replay.")
    parser.add_argument("--week-start", help="Monday date YYYY-MM-DD; default is the latest completed/closing week.")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output-root", default=str(_default_output_root()))
    parser.add_argument("--warmup-bars", type=int, default=60)
    parser.add_argument("--history-bars", type=int, default=60)
    parser.add_argument("--chunk-size", type=int, default=250)
    parser.add_argument("--max-chunks", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.warmup_bars < 50 or args.history_bars < 50:
        raise ValueError("warmup-bars and history-bars must be >= 50")
    if args.chunk_size <= 0 or args.max_chunks <= 0:
        raise ValueError("chunk-size and max-chunks must be positive")

    unsupported = tuple(symbol for symbol in args.symbols if symbol not in ALLOWED_TRADING_SYMBOLS)
    if unsupported:
        raise ValueError(f"Unsupported backtest symbols: {unsupported}; allowed={ALLOWED_TRADING_SYMBOLS}")

    start_day = date.fromisoformat(args.week_start) if args.week_start else default_week_start(datetime.now(UTC))
    if start_day.weekday() != 0:
        raise ValueError("week-start must be a Monday")
    week_start = _utc_midnight(start_day)
    week_end = week_start + timedelta(days=5)
    label = f"{start_day.isoformat()}_{(start_day + timedelta(days=4)).isoformat()}"
    output_dir = Path(args.output_root).expanduser() / "data" / label
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        raise RuntimeError("MT5_CONNECT_FAILED")

    metadata: dict[str, object] = {
        "schema": "forex-ai-backtest-source-v1",
        "source": "MT5 broker OHLC history",
        "week_start_utc": week_start.isoformat(),
        "week_end_utc_exclusive": week_end.isoformat(),
        "warmup_bars": args.warmup_bars,
        "history_bars": args.history_bars,
        "symbols": {},
    }
    try:
        constants = client.constants()
        available = client.symbol_candidates(tuple(args.symbols))
        for base in args.symbols:
            actual = resolve_symbol_strict(base, available)
            if actual is None:
                raise RuntimeError(f"SYMBOL_MAPPING_UNRESOLVED:{base}")
            info = client.symbol_info(actual)
            if not info or not info.get("point"):
                raise RuntimeError(f"SYMBOL_INFO_UNAVAILABLE:{actual}")

            rows_by_tf: dict[str, list[dict[str, object]]] = {}
            symbol_meta: dict[str, object] = {
                "actual_symbol": actual,
                "point": float(info["point"]),
                "timeframes": {},
            }
            for timeframe_name in TIMEFRAMES:
                rows = _fetch_window(
                    client,
                    symbol=actual,
                    timeframe=constants[timeframe_name],
                    week_start=week_start,
                    week_end=week_end,
                    warmup_bars=args.warmup_bars,
                    chunk_size=args.chunk_size,
                    max_chunks=args.max_chunks,
                )
                rows_by_tf[timeframe_name] = rows
                raw_path = output_dir / actual / f"{timeframe_name}.json"
                _remove_if_requested((raw_path,), overwrite=args.overwrite)
                _write_json(raw_path, rows)
                target_count = sum(week_start.timestamp() <= _row_time(row) < week_end.timestamp() for row in rows)
                symbol_meta["timeframes"][timeframe_name] = {
                    "path": str(raw_path),
                    "rows_total": len(rows),
                    "rows_target_week": target_count,
                    "first_time_utc": datetime.fromtimestamp(_row_time(rows[0]), UTC).isoformat(),
                    "last_time_utc": datetime.fromtimestamp(_row_time(rows[-1]), UTC).isoformat(),
                    "sha256": _sha256(raw_path),
                }

            events = build_replay_events_from_mt5_bars(
                symbol=actual,
                point=float(info["point"]),
                m15_rows=rows_by_tf["M15"],
                h1_rows=rows_by_tf["H1"],
                h4_rows=rows_by_tf["H4"],
                history_bars=args.history_bars,
            )
            replay_events = tuple(event for event in events if week_start < event.clock_utc <= week_end)
            if not replay_events:
                raise RuntimeError(f"NO_REPLAY_EVENTS:{actual}")
            replay_path = output_dir / actual / "replay.jsonl"
            manifest_path = replay_path.with_suffix(replay_path.suffix + ".manifest.json")
            _remove_if_requested((replay_path, manifest_path), overwrite=args.overwrite)
            manifest = freeze_replay_dataset(
                replay_events,
                data_path=replay_path,
                source_id=f"mt5-week:{actual}:{label}:warmup={args.warmup_bars}:history={args.history_bars}",
                created_at_utc=datetime.now(UTC),
            )
            symbol_meta["replay"] = {
                "path": str(replay_path),
                "records": manifest.record_count,
                "first_clock_utc": manifest.first_clock_utc,
                "last_clock_utc": manifest.last_clock_utc,
                "dataset_sha256": manifest.dataset_sha256,
                "event_fingerprint": manifest.event_fingerprint,
            }
            metadata["symbols"][base] = symbol_meta
            print(f"{base}->{actual}: replay_records={manifest.record_count} output={replay_path}")
    finally:
        client.close()

    metadata_path = output_dir / "source_manifest.json"
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            existing.get("week_start_utc") != metadata["week_start_utc"]
            or existing.get("week_end_utc_exclusive") != metadata["week_end_utc_exclusive"]
        ):
            raise RuntimeError("SOURCE_MANIFEST_WEEK_MISMATCH")
        merged_symbols = {
            key: value for key, value in dict(existing.get("symbols") or {}).items()
            if key in ALLOWED_TRADING_SYMBOLS
        }
        merged_symbols.update(metadata["symbols"])
        metadata["symbols"] = merged_symbols
    metadata["created_at_utc"] = datetime.now(UTC).isoformat()
    _write_json(metadata_path, metadata)
    print(f"week={label}")
    print(f"manifest={metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
