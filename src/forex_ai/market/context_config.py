from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from forex_ai.config import PROJECT_ROOT
from forex_ai.strategy.v1.contracts import fingerprint

SCHEMA_VERSION = 1
ALLOWED_TIMEFRAMES = ("H4", "D1")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeframeStructureConfig(_StrictModel):
    history_bars: int = Field(ge=20, le=2000)
    atr_period: int = Field(ge=2, le=500)
    pivot_left: int = Field(ge=1, le=50)
    pivot_right: int = Field(ge=1, le=50)
    cluster_distance_atr: float = Field(gt=0, le=10)
    zone_half_width_atr: float = Field(gt=0, le=10)
    timeframe_weight: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _history_is_sufficient(self) -> "TimeframeStructureConfig":
        required = max(self.atr_period + 1, self.pivot_left + self.pivot_right + 3)
        if self.history_bars < required:
            raise ValueError(f"history_bars must be >= {required}")
        return self


class HigherTimeframeStructureConfig(_StrictModel):
    enabled: bool = True
    refresh_seconds: int = Field(ge=60, le=86400)
    max_support_levels: int = Field(ge=1, le=50)
    max_resistance_levels: int = Field(ge=1, le=50)
    timeframes: dict[str, TimeframeStructureConfig]

    @model_validator(mode="after")
    def _validate_timeframes(self) -> "HigherTimeframeStructureConfig":
        keys = tuple(self.timeframes.keys())
        if set(keys) != set(ALLOWED_TIMEFRAMES):
            raise ValueError(f"timeframes must be exactly {ALLOWED_TIMEFRAMES}")
        return self


@dataclass(frozen=True)
class MarketContextConfigSnapshot:
    schema_version: int
    higher_timeframe_structure: HigherTimeframeStructureConfig
    fingerprint: str
    source_path: Path
    loaded_from_last_good: bool = False
    rejected_error: str | None = None

    @property
    def max_history_bars(self) -> int:
        return max(config.history_bars for config in self.higher_timeframe_structure.timeframes.values())


def _bundled_path() -> Path:
    candidates = [
        PROJECT_ROOT / "config" / "market-context.yaml",
        Path.home() / "apps" / "forex-ai" / "current" / "config" / "market-context.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def runtime_market_context_path() -> Path:
    explicit = os.getenv("FOREX_AI_MARKET_CONTEXT_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".config" / "forex-ai" / "market-context.yaml"


def last_good_path(active_path: Path | None = None) -> Path:
    active = active_path or runtime_market_context_path()
    return active.with_name("market-context.last-good.yaml")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("market-context root must be a mapping")
    return raw


def _compile(
    raw: Mapping[str, Any],
    *,
    source_path: Path,
    loaded_from_last_good: bool = False,
    rejected_error: str | None = None,
) -> MarketContextConfigSnapshot:
    schema_version = int(raw.get("schema_version", 0))
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported market-context schema_version={schema_version}")
    unknown = set(raw) - {"schema_version", "higher_timeframe_structure"}
    if unknown:
        raise ValueError(f"unsupported market-context keys: {sorted(unknown)}")
    context_raw = raw.get("higher_timeframe_structure")
    if not isinstance(context_raw, Mapping):
        raise ValueError("higher_timeframe_structure mapping is required")
    model = HigherTimeframeStructureConfig.model_validate(dict(context_raw))
    canonical = {
        "schema_version": schema_version,
        "higher_timeframe_structure": model.model_dump(mode="python"),
    }
    return MarketContextConfigSnapshot(
        schema_version=schema_version,
        higher_timeframe_structure=model,
        fingerprint=fingerprint(canonical),
        source_path=source_path,
        loaded_from_last_good=loaded_from_last_good,
        rejected_error=rejected_error,
    )


def load_market_context_snapshot(
    path: Path | None = None,
    *,
    allow_last_good: bool | None = None,
) -> MarketContextConfigSnapshot:
    explicit = path is not None
    active = Path(path).expanduser() if path is not None else runtime_market_context_path()
    if allow_last_good is None:
        allow_last_good = not explicit

    if not active.is_file():
        if explicit:
            raise FileNotFoundError(active)
        bundled = _bundled_path()
        if not bundled.is_file():
            raise FileNotFoundError(f"market-context config missing: active={active} bundled={bundled}")
        active.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, active)

    try:
        snapshot = _compile(_read_yaml(active), source_path=active)
    except Exception as exc:
        if not allow_last_good:
            raise
        fallback = last_good_path(active)
        if not fallback.is_file():
            raise ValueError(f"market-context config invalid and no last-known-good exists: {exc}") from exc
        return _compile(
            _read_yaml(fallback),
            source_path=fallback,
            loaded_from_last_good=True,
            rejected_error=f"{type(exc).__name__}: {exc}",
        )

    if allow_last_good:
        fallback = last_good_path(active)
        fallback.parent.mkdir(parents=True, exist_ok=True)
        if not fallback.is_file() or fallback.read_bytes() != active.read_bytes():
            temp = fallback.with_suffix(fallback.suffix + ".tmp")
            shutil.copy2(active, temp)
            os.replace(temp, fallback)
    return snapshot
