from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

UTC = timezone.utc
SEGMENT_SCHEMA = "forex-ai-scalping-segment-v1"
DEFAULT_SEGMENT_DAYS: Mapping[str, int] = {
    "M5": 14,
    "M15": 28,
    "H1": 56,
    "H4": 180,
    "D1": 730,
}
MIN_WARMUP_DAYS: Mapping[str, int] = {
    "M5": 7,
    "M15": 7,
    "H1": 21,
    "H4": 42,
    "D1": 252,
}


@dataclass(frozen=True)
class SegmentSpec:
    kind: str
    index: int
    start_utc: datetime
    end_utc: datetime

    @property
    def key(self) -> str:
        return f"{self.kind}-{self.index:03d}-{self.start_utc:%Y%m%dT%H%M%SZ}-{self.end_utc:%Y%m%dT%H%M%SZ}"


@dataclass(frozen=True)
class SegmentResult:
    spec: SegmentSpec
    path: Path
    sha256: str
    row_count: int
    first_time: int | None
    last_time: int | None
    cache_hit: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_time(row: Mapping[str, object]) -> int:
    return int(row["time"])


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temp, path)


def target_segments(range_start: datetime, range_end: datetime, *, days: int) -> tuple[SegmentSpec, ...]:
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise ValueError("segment boundaries must be timezone-aware")
    if range_start >= range_end or days <= 0:
        raise ValueError("invalid target segment range")
    cursor = range_start.astimezone(UTC)
    end = range_end.astimezone(UTC)
    specs: list[SegmentSpec] = []
    index = 0
    while cursor < end:
        segment_end = min(cursor + timedelta(days=days), end)
        specs.append(SegmentSpec("target", index, cursor, segment_end))
        cursor = segment_end
        index += 1
    return tuple(specs)


def warmup_span_days(timeframe: str, *, timeframe_seconds: int, warmup_bars: int) -> int:
    if warmup_bars <= 0 or timeframe_seconds <= 0:
        raise ValueError("warmup values must be positive")
    market_days = timeframe_seconds * warmup_bars / 86400.0
    # 7/5 converts trading days to calendar days. Extra 1.5x absorbs holidays,
    # sparse broker history and range-boundary inclusivity.
    estimated = int(market_days * (7.0 / 5.0) * 1.5) + 1
    return max(int(MIN_WARMUP_DAYS[timeframe]), estimated)


def warmup_spec(
    timeframe: str,
    *,
    timeframe_seconds: int,
    warmup_bars: int,
    range_start: datetime,
    expansion: int = 1,
) -> SegmentSpec:
    days = warmup_span_days(timeframe, timeframe_seconds=timeframe_seconds, warmup_bars=warmup_bars) * expansion
    start = range_start.astimezone(UTC) - timedelta(days=days)
    return SegmentSpec("warmup", expansion, start, range_start.astimezone(UTC))


def _normalize_rows(rows: Sequence[Mapping[str, object]], spec: SegmentSpec) -> list[dict[str, object]]:
    start_ts = int(spec.start_utc.timestamp())
    end_ts = int(spec.end_utc.timestamp())
    merged: dict[int, dict[str, object]] = {}
    for row in rows:
        timestamp = _row_time(row)
        if start_ts <= timestamp < end_ts:
            merged[timestamp] = dict(row)
    return [merged[key] for key in sorted(merged)]


def _validate_rows(rows: Sequence[Mapping[str, object]]) -> None:
    timestamps = [_row_time(row) for row in rows]
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        raise ValueError("segment rows must be strictly time ordered and unique")


def _meta_path(segment_path: Path) -> Path:
    return segment_path.with_suffix(segment_path.suffix + ".meta.json")


def _load_valid_cache(segment_path: Path, spec: SegmentSpec) -> SegmentResult | None:
    meta_path = _meta_path(segment_path)
    if not segment_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("schema") != SEGMENT_SCHEMA or meta.get("segment_key") != spec.key:
            return None
        digest = _sha256(segment_path)
        if meta.get("sha256") != digest:
            return None
        rows = json.loads(segment_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            return None
        _validate_rows(rows)
        return SegmentResult(
            spec=spec,
            path=segment_path,
            sha256=digest,
            row_count=len(rows),
            first_time=_row_time(rows[0]) if rows else None,
            last_time=_row_time(rows[-1]) if rows else None,
            cache_hit=True,
        )
    except Exception:
        return None


def fetch_segment(
    *,
    fetch_range: Callable[[float, float], Sequence[Mapping[str, object]]],
    spec: SegmentSpec,
    segment_path: Path,
    overwrite: bool = False,
) -> SegmentResult:
    if not overwrite:
        cached = _load_valid_cache(segment_path, spec)
        if cached is not None:
            return cached

    rows = _normalize_rows(
        fetch_range(spec.start_utc.timestamp(), spec.end_utc.timestamp()),
        spec,
    )
    _validate_rows(rows)
    if not rows:
        raise RuntimeError(f"EMPTY_SEGMENT:{spec.key}")
    _atomic_json(segment_path, rows)
    digest = _sha256(segment_path)
    meta = {
        "schema": SEGMENT_SCHEMA,
        "segment_key": spec.key,
        "kind": spec.kind,
        "index": spec.index,
        "start_utc": spec.start_utc.isoformat(),
        "end_utc_exclusive": spec.end_utc.isoformat(),
        "rows": len(rows),
        "first_time_utc": datetime.fromtimestamp(_row_time(rows[0]), UTC).isoformat(),
        "last_time_utc": datetime.fromtimestamp(_row_time(rows[-1]), UTC).isoformat(),
        "sha256": digest,
    }
    _atomic_json(_meta_path(segment_path), meta)
    return SegmentResult(
        spec=spec,
        path=segment_path,
        sha256=digest,
        row_count=len(rows),
        first_time=_row_time(rows[0]),
        last_time=_row_time(rows[-1]),
        cache_hit=False,
    )


def merge_segment_files(
    *,
    segment_results: Sequence[SegmentResult],
    output_path: Path,
    range_start: datetime,
    range_end: datetime,
    warmup_bars: int,
) -> list[dict[str, object]]:
    if warmup_bars <= 0:
        raise ValueError("warmup_bars must be positive")
    start_ts = int(range_start.timestamp())
    end_ts = int(range_end.timestamp())
    merged: dict[int, dict[str, object]] = {}
    for result in segment_results:
        rows = json.loads(result.path.read_text(encoding="utf-8"))
        for row in rows:
            merged[_row_time(row)] = dict(row)
    ordered = [merged[key] for key in sorted(merged)]
    _validate_rows(ordered)
    before = [row for row in ordered if _row_time(row) < start_ts]
    target = [row for row in ordered if start_ts <= _row_time(row) < end_ts]
    if len(before) < warmup_bars:
        raise RuntimeError(f"INSUFFICIENT_SEGMENTED_WARMUP:have={len(before)}:need={warmup_bars}")
    if not target:
        raise RuntimeError("SEGMENTED_TARGET_EMPTY")
    final_rows = [*before[-warmup_bars:], *target]
    _atomic_json(output_path, final_rows)
    return final_rows
