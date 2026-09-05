from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, TimeframeSnapshot, fingerprint

from .replay import ReplayEvent

DATASET_SCHEMA = "forex-ai-replay-v1"


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("dataset timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _candle_payload(candle: Candle) -> dict[str, Any]:
    return {
        "time_utc": _iso(candle.time_utc),
        "open": float(candle.open),
        "high": float(candle.high),
        "low": float(candle.low),
        "close": float(candle.close),
        "volume": float(candle.volume),
    }


def _timeframe_payload(timeframe: TimeframeSnapshot) -> dict[str, Any]:
    return {
        "timeframe": timeframe.timeframe,
        "closed_bars": [_candle_payload(bar) for bar in timeframe.closed_bars],
        "current_bar": _candle_payload(timeframe.current_bar) if timeframe.current_bar is not None else None,
    }


def event_payload(event: ReplayEvent) -> dict[str, Any]:
    snapshot = event.snapshot
    return {
        "clock_utc": _iso(event.clock_utc),
        "snapshot": {
            "symbol": snapshot.symbol,
            "captured_at_utc": _iso(snapshot.captured_at_utc),
            "market_time_msc": snapshot.market_time_msc,
            "bid": float(snapshot.bid),
            "ask": float(snapshot.ask),
            "timeframes": {
                name: _timeframe_payload(timeframe)
                for name, timeframe in sorted(snapshot.timeframes.items())
            },
            "spread_cost": float(snapshot.spread_cost),
            "commission_cost": float(snapshot.commission_cost),
            "metadata": dict(snapshot.metadata),
            **({"context": dict(snapshot.context)} if snapshot.context else {}),
        },
    }


def _parse_candle(payload: dict[str, Any]) -> Candle:
    return Candle(
        time_utc=datetime.fromisoformat(str(payload["time_utc"])),
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        volume=float(payload.get("volume", 0.0)),
    )


def parse_event(payload: dict[str, Any]) -> ReplayEvent:
    snapshot_payload = payload["snapshot"]
    timeframes = {
        str(name): TimeframeSnapshot(
            timeframe=str(tf_payload["timeframe"]),
            closed_bars=tuple(_parse_candle(bar) for bar in tf_payload.get("closed_bars", ())),
            current_bar=_parse_candle(tf_payload["current_bar"]) if tf_payload.get("current_bar") is not None else None,
        )
        for name, tf_payload in snapshot_payload["timeframes"].items()
    }
    snapshot = MarketSnapshot(
        symbol=str(snapshot_payload["symbol"]),
        captured_at_utc=datetime.fromisoformat(str(snapshot_payload["captured_at_utc"])),
        market_time_msc=int(snapshot_payload["market_time_msc"]),
        bid=float(snapshot_payload["bid"]),
        ask=float(snapshot_payload["ask"]),
        timeframes=timeframes,
        spread_cost=float(snapshot_payload.get("spread_cost", 0.0)),
        commission_cost=float(snapshot_payload.get("commission_cost", 0.0)),
        metadata=dict(snapshot_payload.get("metadata") or {}),
        context=dict(snapshot_payload.get("context") or {}),
    )
    return ReplayEvent(clock_utc=datetime.fromisoformat(str(payload["clock_utc"])), snapshot=snapshot)


def replay_event_fingerprint(events: Iterable[ReplayEvent]) -> str:
    return fingerprint([event_payload(event) for event in events])


@dataclass(frozen=True)
class ReplayDatasetManifest:
    schema: str
    source_id: str
    dataset_sha256: str
    event_fingerprint: str
    record_count: int
    first_clock_utc: str | None
    last_clock_utc: str | None
    symbols: tuple[str, ...]
    created_at_utc: str

    @property
    def fingerprint(self) -> str:
        return fingerprint(asdict(self))

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["manifest_fingerprint"] = self.fingerprint
        return payload


@dataclass(frozen=True)
class FrozenReplayDataset:
    manifest: ReplayDatasetManifest
    events: tuple[ReplayEvent, ...]


def freeze_replay_dataset(
    events: Iterable[ReplayEvent],
    *,
    data_path: Path,
    source_id: str,
    created_at_utc: datetime,
) -> ReplayDatasetManifest:
    if not source_id.strip():
        raise ValueError("source_id is required")
    if created_at_utc.tzinfo is None:
        raise ValueError("created_at_utc must be timezone-aware")
    manifest_path = data_path.with_suffix(data_path.suffix + ".manifest.json")
    if data_path.exists() or manifest_path.exists():
        raise FileExistsError("frozen dataset paths already exist")
    rows = tuple(events)
    if any(a.clock_utc >= b.clock_utc for a, b in zip(rows[:-1], rows[1:])):
        raise ValueError("dataset events must be strictly ordered")
    lines = tuple(_canonical_json(event_payload(event)) for event in rows)
    raw = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
    symbols = tuple(sorted({event.snapshot.symbol for event in rows}))
    manifest = ReplayDatasetManifest(
        schema=DATASET_SCHEMA,
        source_id=source_id,
        dataset_sha256=_sha256_bytes(raw),
        event_fingerprint=replay_event_fingerprint(rows),
        record_count=len(rows),
        first_clock_utc=_iso(rows[0].clock_utc) if rows else None,
        last_clock_utc=_iso(rows[-1].clock_utc) if rows else None,
        symbols=symbols,
        created_at_utc=_iso(created_at_utc),
    )
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_bytes(raw)
    manifest_path.write_text(json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _manifest_from_payload(payload: dict[str, Any]) -> ReplayDatasetManifest:
    manifest = ReplayDatasetManifest(
        schema=str(payload["schema"]),
        source_id=str(payload["source_id"]),
        dataset_sha256=str(payload["dataset_sha256"]),
        event_fingerprint=str(payload["event_fingerprint"]),
        record_count=int(payload["record_count"]),
        first_clock_utc=str(payload["first_clock_utc"]) if payload.get("first_clock_utc") is not None else None,
        last_clock_utc=str(payload["last_clock_utc"]) if payload.get("last_clock_utc") is not None else None,
        symbols=tuple(str(value) for value in payload.get("symbols", ())),
        created_at_utc=str(payload["created_at_utc"]),
    )
    if payload.get("manifest_fingerprint") != manifest.fingerprint:
        raise ValueError("dataset manifest fingerprint mismatch")
    return manifest


def load_frozen_replay_dataset(data_path: Path) -> FrozenReplayDataset:
    manifest_path = data_path.with_suffix(data_path.suffix + ".manifest.json")
    if not data_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("dataset or manifest missing")
    raw = data_path.read_bytes()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest = _manifest_from_payload(payload)
    if manifest.schema != DATASET_SCHEMA:
        raise ValueError(f"unsupported dataset schema {manifest.schema}")
    if _sha256_bytes(raw) != manifest.dataset_sha256:
        raise ValueError("dataset byte hash mismatch")
    lines = tuple(line for line in raw.decode("utf-8").splitlines() if line.strip())
    events = tuple(parse_event(json.loads(line)) for line in lines)
    if len(events) != manifest.record_count:
        raise ValueError("dataset record count mismatch")
    if replay_event_fingerprint(events) != manifest.event_fingerprint:
        raise ValueError("dataset event fingerprint mismatch")
    if tuple(sorted({event.snapshot.symbol for event in events})) != manifest.symbols:
        raise ValueError("dataset symbol set mismatch")
    first = _iso(events[0].clock_utc) if events else None
    last = _iso(events[-1].clock_utc) if events else None
    if first != manifest.first_clock_utc or last != manifest.last_clock_utc:
        raise ValueError("dataset time boundary mismatch")
    return FrozenReplayDataset(manifest=manifest, events=events)
