from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from forex_ai.market.context_config import HigherTimeframeStructureConfig
from forex_ai.market.structure import extract_higher_timeframe_levels, project_higher_timeframe_structure
from forex_ai.research.replay import ReplayEvent
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot

UTC = timezone.utc
SCHEMA = "forex-ai-scalping-source-v1"
POINTER_SCHEMA = "forex-ai-scalping-dataset-pointer-v1"
BUILDER_VERSION = "scalping-stream-v1"
STRATEGY_TIMEFRAMES = ("M5", "M15", "H1")
CONTEXT_TIMEFRAMES = ("H4", "D1")
TIMEFRAME_SECONDS = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


@dataclass(frozen=True)
class Partition:
    name: str
    start_utc: datetime
    end_utc_exclusive: datetime

    def contains(self, clock: datetime) -> bool:
        instant = clock.astimezone(UTC)
        # Match the canonical replay boundary: a decision at exactly partition
        # start would be based on a bar that opened before the partition.
        return self.start_utc < instant < self.end_utc_exclusive


@dataclass(frozen=True)
class ScalpingDataset:
    pointer_path: Path
    dataset_root: Path
    source_manifest_path: Path
    source_manifest_sha256: str
    manifest: Mapping[str, object]

    @property
    def range_start_utc(self) -> datetime:
        return datetime.fromisoformat(str(self.manifest["range_start_utc"]))

    @property
    def range_end_utc_exclusive(self) -> datetime:
        return datetime.fromisoformat(str(self.manifest["range_end_utc_exclusive"]))

    @property
    def history_bars(self) -> int:
        return int(self.manifest["history_bars"])

    @property
    def builder_version(self) -> str:
        return str(self.manifest["builder_version"])

    @property
    def dataset_source_fingerprint(self) -> str:
        return str(self.manifest["dataset_source_fingerprint"])

    @property
    def partitions(self) -> tuple[Partition, ...]:
        raw = self.manifest.get("partitions") or {}
        return tuple(
            Partition(
                name=str(name),
                start_utc=datetime.fromisoformat(str(value["start_utc"])),
                end_utc_exclusive=datetime.fromisoformat(str(value["end_utc_exclusive"])),
            )
            for name, value in raw.items()
        )

    def partition_for(self, clock: datetime) -> str | None:
        for partition in self.partitions:
            if partition.contains(clock):
                return partition.name
        return None

    def symbol_manifest(self, base_symbol: str) -> Mapping[str, object]:
        symbols = self.manifest.get("symbols") or {}
        if base_symbol not in symbols:
            raise KeyError(f"scalping dataset symbol missing: {base_symbol}")
        return symbols[base_symbol]

    def load_raw(self, base_symbol: str) -> dict[str, list[dict[str, object]]]:
        symbol = self.symbol_manifest(base_symbol)
        timeframes = symbol.get("timeframes") or {}
        rows_by_tf: dict[str, list[dict[str, object]]] = {}
        for timeframe in (*STRATEGY_TIMEFRAMES, *CONTEXT_TIMEFRAMES):
            metadata = timeframes.get(timeframe)
            if not isinstance(metadata, Mapping):
                raise ValueError(f"timeframe metadata missing: {base_symbol}:{timeframe}")
            path = Path(str(metadata["path"])).expanduser()
            if not path.is_file():
                raise FileNotFoundError(path)
            digest = _sha256(path)
            if digest != str(metadata["sha256"]):
                raise ValueError(f"raw hash mismatch: {base_symbol}:{timeframe}")
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                raise ValueError(f"raw timeframe must be a list: {path}")
            timestamps = [_row_time(row) for row in rows]
            if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
                raise ValueError(f"raw timeframe not strictly ordered: {base_symbol}:{timeframe}")
            rows_by_tf[timeframe] = rows
        return rows_by_tf

    def iter_events(
        self,
        base_symbol: str,
        *,
        partition: str | None = None,
    ) -> Iterator[ReplayEvent]:
        symbol = self.symbol_manifest(base_symbol)
        actual_symbol = str(symbol["actual_symbol"])
        point = float(symbol["point"])
        rows_by_tf = self.load_raw(base_symbol)
        context_config = HigherTimeframeStructureConfig.model_validate(
            dict(self.manifest["market_context_config"])
        )
        context_fingerprint = str(self.manifest["market_context_config_fingerprint"])
        starts_by_tf = {
            name: tuple(_row_time(row) for row in rows)
            for name, rows in rows_by_tf.items()
        }
        selected_partition = None
        if partition is not None:
            matches = [item for item in self.partitions if item.name == partition]
            if not matches:
                raise KeyError(f"unknown scalping partition: {partition}")
            selected_partition = matches[0]

        cached_strategy: dict[str, tuple[int, TimeframeSnapshot]] = {}
        cached_context_key: tuple[int, ...] | None = None
        cached_levels = ()
        cached_context_timeframes: tuple[str, ...] = ()

        for row in rows_by_tf["M5"]:
            start = datetime.fromtimestamp(_row_time(row), UTC)
            clock = start + timedelta(seconds=TIMEFRAME_SECONDS["M5"])
            if not (self.range_start_utc < clock <= self.range_end_utc_exclusive):
                continue
            if selected_partition is not None and not selected_partition.contains(clock):
                continue

            strategy_tfs: dict[str, TimeframeSnapshot] = {}
            for name in STRATEGY_TIMEFRAMES:
                cutoff_start = int(clock.timestamp()) - TIMEFRAME_SECONDS[name]
                end = bisect_right(starts_by_tf[name], cutoff_start)
                cached = cached_strategy.get(name)
                if cached is None or cached[0] != end:
                    begin = max(0, end - self.history_bars)
                    snapshot = TimeframeSnapshot(
                        name,
                        tuple(_candle(item) for item in rows_by_tf[name][begin:end]),
                    )
                    cached_strategy[name] = (end, snapshot)
                strategy_tfs[name] = cached_strategy[name][1]
            if min(len(tf.closed_bars) for tf in strategy_tfs.values()) < 50:
                continue

            context_ends = tuple(
                bisect_right(
                    starts_by_tf[name],
                    int(clock.timestamp()) - TIMEFRAME_SECONDS[name],
                )
                for name in CONTEXT_TIMEFRAMES
            )
            if cached_context_key != context_ends:
                context_tfs: dict[str, TimeframeSnapshot] = {}
                for name, end in zip(CONTEXT_TIMEFRAMES, context_ends, strict=True):
                    keep = context_config.timeframes[name].history_bars
                    begin = max(0, end - keep)
                    context_tfs[name] = TimeframeSnapshot(
                        name,
                        tuple(_candle(item) for item in rows_by_tf[name][begin:end]),
                    )
                cached_levels, cached_context_timeframes = extract_higher_timeframe_levels(
                    timeframes=context_tfs,
                    config=context_config,
                )
                cached_context_key = context_ends

            close = float(row["close"])
            spread_points = max(float(row.get("spread") or 0.0), 0.0)
            spread = spread_points * point
            bid = close
            ask = close + spread
            structure = project_higher_timeframe_structure(
                levels=cached_levels,
                source_timeframes=cached_context_timeframes,
                reference_price=(bid + ask) / 2.0,
                captured_at_utc=clock,
                config=context_config,
                config_fingerprint=context_fingerprint,
            )
            yield ReplayEvent(
                clock,
                MarketSnapshot(
                    symbol=actual_symbol,
                    captured_at_utc=clock,
                    market_time_msc=int(clock.timestamp() * 1000),
                    bid=bid,
                    ask=ask,
                    timeframes=strategy_tfs,
                    spread_cost=spread,
                    commission_cost=0.0,
                    metadata={
                        "source": "mt5_broker_history",
                        "profile": "scalping_v1",
                        "anchor_timeframe": "M5",
                        "historical_spread_points": spread_points,
                        "partition": self.partition_for(clock),
                    },
                    context={"higher_timeframe_structure": structure.to_dict()},
                ),
            )


def load_scalping_dataset(pointer_path: Path) -> ScalpingDataset:
    pointer_path = pointer_path.expanduser()
    if not pointer_path.is_file():
        raise FileNotFoundError(pointer_path)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("schema") != POINTER_SCHEMA:
        raise ValueError(f"unsupported scalping pointer schema: {pointer.get('schema')}")
    manifest_path = Path(str(pointer["source_manifest"])).expanduser()
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    digest = _sha256(manifest_path)
    expected = str(pointer["source_manifest_sha256"])
    if digest != expected:
        raise ValueError("scalping source manifest hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA:
        raise ValueError(f"unsupported scalping source schema: {manifest.get('schema')}")
    dataset_root = Path(str(pointer["dataset_root"])).expanduser()
    return ScalpingDataset(
        pointer_path=pointer_path,
        dataset_root=dataset_root,
        source_manifest_path=manifest_path,
        source_manifest_sha256=digest,
        manifest=manifest,
    )
