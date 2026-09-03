from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Mapping

from forex_ai.strategy.v1.contracts import fingerprint


class MacroRegime(str, Enum):
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    TIGHTENING = "TIGHTENING"
    EASING = "EASING"
    MIXED = "MIXED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class SourceEvidence:
    source_id: str
    observed_at_utc: datetime
    content_fingerprint: str
    quality: str = "UNKNOWN"


@dataclass(frozen=True)
class ScheduledEvent:
    event_id: str
    name: str
    starts_at_utc: datetime
    importance: str
    affected_assets: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventBlackout:
    event_id: str
    starts_at_utc: datetime
    ends_at_utc: datetime
    reason: str

    def contains(self, moment_utc: datetime) -> bool:
        return self.starts_at_utc <= moment_utc <= self.ends_at_utc


@dataclass(frozen=True)
class FreshnessPolicy:
    ttl_seconds: int
    event_pre_seconds: int = 900
    event_post_seconds: int = 300

    def is_fresh(self, captured_at_utc: datetime, now_utc: datetime) -> bool:
        return now_utc - captured_at_utc <= timedelta(seconds=self.ttl_seconds)


@dataclass(frozen=True)
class MacroSnapshot:
    snapshot_id: str
    captured_at_utc: datetime
    regime: MacroRegime
    sources: tuple[SourceEvidence, ...]
    scheduled_events: tuple[ScheduledEvent, ...]
    values: Mapping[str, Any] = field(default_factory=dict)
    unavailable_reason: str | None = None
    source_fingerprint: str = ""
    input_fingerprint: str = ""
    model_fingerprint: str = ""
    config_fingerprint: str = ""

    @property
    def cache_key(self) -> str:
        return fingerprint({
            "source": self.source_fingerprint,
            "input": self.input_fingerprint,
            "model": self.model_fingerprint,
            "config": self.config_fingerprint,
        })

    @classmethod
    def unavailable(cls, *, now_utc: datetime, reason: str, source_fingerprint: str = "") -> "MacroSnapshot":
        seed = fingerprint({"time": now_utc.astimezone(timezone.utc), "reason": reason, "source": source_fingerprint})
        return cls(seed[:24], now_utc.astimezone(timezone.utc), MacroRegime.UNAVAILABLE, (), (), {}, reason, source_fingerprint)


def build_blackouts(events: tuple[ScheduledEvent, ...], policy: FreshnessPolicy) -> tuple[EventBlackout, ...]:
    return tuple(
        EventBlackout(
            event.event_id,
            event.starts_at_utc - timedelta(seconds=policy.event_pre_seconds),
            event.starts_at_utc + timedelta(seconds=policy.event_post_seconds),
            f"scheduled:{event.name}",
        )
        for event in events
        if event.importance.upper() in {"HIGH", "CRITICAL"}
    )


def calendar_state(snapshot: MacroSnapshot | None, now_utc: datetime, policy: FreshnessPolicy) -> str:
    if snapshot is None or snapshot.regime == MacroRegime.UNAVAILABLE:
        return "UNAVAILABLE"
    if not policy.is_fresh(snapshot.captured_at_utc, now_utc):
        return "UNAVAILABLE"
    if any(blackout.contains(now_utc) for blackout in build_blackouts(snapshot.scheduled_events, policy)):
        return "BLACKOUT"
    return "AVAILABLE"
