from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from backtest.scalping_segments import fetch_segment, merge_segment_files, target_segments, warmup_spec

UTC = timezone.utc
START = datetime(2026, 7, 13, tzinfo=UTC)
END = datetime(2026, 7, 20, tzinfo=UTC)


def _rows(start_ts: int, end_ts: int, step: int = 300):
    out = []
    for timestamp in range(start_ts, end_ts, step):
        price = 1.1 + (timestamp - start_ts) / step * 0.00001
        out.append({
            "time": timestamp,
            "open": price,
            "high": price + 0.0001,
            "low": price - 0.0001,
            "close": price + 0.00002,
            "tick_volume": 100,
            "spread": 2,
            "real_volume": 0,
        })
    return out


def test_target_segments_are_half_open_and_cover_range_exactly():
    specs = target_segments(START, END, days=3)
    assert [spec.start_utc for spec in specs] == [START, START + timedelta(days=3), START + timedelta(days=6)]
    assert [spec.end_utc for spec in specs] == [START + timedelta(days=3), START + timedelta(days=6), END]


def test_segment_cache_hit_and_hash_corruption_refetch(tmp_path):
    spec = target_segments(START, START + timedelta(days=1), days=1)[0]
    path = tmp_path / "segment.json"
    calls = {"count": 0}

    def fetch(start_ts, end_ts):
        calls["count"] += 1
        return _rows(int(start_ts), int(end_ts) + 300)

    first = fetch_segment(fetch_range=fetch, spec=spec, segment_path=path)
    second = fetch_segment(fetch_range=fetch, spec=spec, segment_path=path)
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert calls["count"] == 1

    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    third = fetch_segment(fetch_range=fetch, spec=spec, segment_path=path)
    assert third.cache_hit is False
    assert calls["count"] == 2


def test_merge_deduplicates_boundaries_and_trims_warmup(tmp_path):
    warm_spec = warmup_spec("M5", timeframe_seconds=300, warmup_bars=3, range_start=START)
    target_spec = target_segments(START, START + timedelta(minutes=20), days=1)[0]

    def fetch(start_ts, end_ts):
        return _rows(int(start_ts), int(end_ts) + 300)

    warm = fetch_segment(fetch_range=fetch, spec=warm_spec, segment_path=tmp_path / "warm.json")
    target = fetch_segment(fetch_range=fetch, spec=target_spec, segment_path=tmp_path / "target.json")
    output = tmp_path / "merged.json"
    rows = merge_segment_files(
        segment_results=[warm, target],
        output_path=output,
        range_start=START,
        range_end=START + timedelta(minutes=20),
        warmup_bars=3,
    )
    before = [row for row in rows if int(row["time"]) < int(START.timestamp())]
    target_rows = [row for row in rows if int(row["time"]) >= int(START.timestamp())]
    assert len(before) == 3
    assert len(target_rows) == 4
    timestamps = [int(row["time"]) for row in rows]
    assert timestamps == sorted(set(timestamps))
    assert json.loads(output.read_text(encoding="utf-8")) == rows
