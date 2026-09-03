from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping, Sequence


def _canonical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if hasattr(value, "to_dict"):
        return _canonical(value.to_dict())
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    return value


def fingerprint(value: Any) -> str:
    raw = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candle:
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.time_utc.tzinfo is None:
            raise ValueError("timezone-aware candle required")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("candle values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("candle prices must be positive and volume non-negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("invalid OHLC")

    def to_dict(self) -> dict[str, Any]:
        return vars(self)


@dataclass(frozen=True)
class TimeframeSnapshot:
    timeframe: str
    closed_bars: tuple[Candle, ...]
    current_bar: Candle | None = None

    def __post_init__(self) -> None:
        if not self.timeframe:
            raise ValueError("timeframe is required")
        timestamps = tuple(bar.time_utc for bar in self.closed_bars)
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("duplicate closed bars")
        if any(a >= b for a, b in zip(timestamps[:-1], timestamps[1:])):
            raise ValueError("closed bars must be strictly ordered")
        if self.current_bar is not None and timestamps and self.current_bar.time_utc <= timestamps[-1]:
            raise ValueError("current bar must be newer than closed bars")

    @classmethod
    def from_sequence(cls, timeframe: str, bars: Sequence[Candle], current_bar: Candle | None = None) -> "TimeframeSnapshot":
        return cls(timeframe, tuple(bars), current_bar)

    def to_dict(self) -> dict[str, Any]:
        return {"timeframe": self.timeframe, "closed_bars": self.closed_bars, "current_bar": self.current_bar}


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    captured_at_utc: datetime
    market_time_msc: int
    bid: float
    ask: float
    timeframes: Mapping[str, TimeframeSnapshot]
    spread_cost: float = 0.0
    commission_cost: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.captured_at_utc.tzinfo is None:
            raise ValueError("timezone-aware snapshot required")
        if self.market_time_msc <= 0:
            raise ValueError("market_time_msc must be positive")
        if not all(math.isfinite(value) for value in (self.bid, self.ask, self.spread_cost, self.commission_cost)):
            raise ValueError("snapshot prices/costs must be finite")
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid bid/ask")
        if self.spread_cost < 0 or self.commission_cost < 0:
            raise ValueError("costs must be non-negative")
        for timeframe in self.timeframes.values():
            if any(bar.time_utc > self.captured_at_utc for bar in timeframe.closed_bars):
                raise ValueError("closed bar cannot be from the future relative to snapshot capture time")
        object.__setattr__(self, "timeframes", MappingProxyType(dict(self.timeframes)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.to_dict())

    @property
    def decision_fingerprint(self) -> str:
        return fingerprint({
            "symbol": self.symbol,
            "captured_at_utc": self.captured_at_utc,
            "market_time_msc": self.market_time_msc,
            "bid": self.bid,
            "ask": self.ask,
            "timeframes": {
                name: {"timeframe": tf.timeframe, "closed_bars": tf.closed_bars}
                for name, tf in self.timeframes.items()
            },
            "spread_cost": self.spread_cost,
            "commission_cost": self.commission_cost,
            "metadata": self.metadata,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "captured_at_utc": self.captured_at_utc,
            "market_time_msc": self.market_time_msc,
            "bid": self.bid,
            "ask": self.ask,
            "timeframes": self.timeframes,
            "spread_cost": self.spread_cost,
            "commission_cost": self.commission_cost,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class StrategyVersion:
    strategy_id: str
    version: str


@dataclass(frozen=True)
class StrategyConfig:
    version: StrategyVersion
    parameters: Mapping[str, Any]
    instrument_class: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def fingerprint(self) -> str:
        return fingerprint({"version": vars(self.version), "parameters": self.parameters, "instrument_class": self.instrument_class})


@dataclass(frozen=True)
class Invalidation:
    kind: str
    price: float
    reason: str


@dataclass(frozen=True)
class DecisionEvidence:
    reason_codes: tuple[str, ...]
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @property
    def evidence_hash(self) -> str:
        return fingerprint({"reason_codes": self.reason_codes, "values": self.values})


@dataclass(frozen=True)
class CandidateEnvelope:
    candidate_id: str
    correlation_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: str
    reference_entry: float
    stop_loss: float
    take_profit: float
    generated_at_utc: datetime
    market_time_msc: int
    expires_at_utc: datetime
    evidence_hash: str
    market_snapshot_fingerprint: str

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("invalid side")
        if self.generated_at_utc.tzinfo is None or self.expires_at_utc.tzinfo is None:
            raise ValueError("timezone-aware timestamps required")


@dataclass(frozen=True)
class StrategyResult:
    candidate: CandidateEnvelope | None
    invalidation: Invalidation | None
    evidence: DecisionEvidence
    no_setup_reason_codes: tuple[str, ...] = ()


def build_candidate(*, snapshot: MarketSnapshot, config: StrategyConfig, side: str, entry: float, stop_loss: float,
                    take_profit: float, generated_at_utc: datetime, expires_at_utc: datetime,
                    evidence: DecisionEvidence) -> CandidateEnvelope:
    seed = {
        "strategy": vars(config.version), "config": config.fingerprint, "snapshot": snapshot.decision_fingerprint,
        "side": side, "entry": entry, "stop": stop_loss, "target": take_profit,
        "generated": generated_at_utc, "expires": expires_at_utc, "evidence": evidence.evidence_hash,
    }
    candidate_id = fingerprint(seed)[:32]
    return CandidateEnvelope(
        candidate_id, f"candidate-{candidate_id}", config.version.strategy_id, config.version.version,
        snapshot.symbol, side, entry, stop_loss, take_profit, generated_at_utc.astimezone(timezone.utc),
        snapshot.market_time_msc, expires_at_utc.astimezone(timezone.utc), evidence.evidence_hash,
        snapshot.decision_fingerprint,
    )
