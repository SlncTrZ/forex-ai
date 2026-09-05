from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from forex_ai.config import PROJECT_ROOT
from forex_ai.strategy.v1.contracts import fingerprint

SCHEMA_VERSION = 1
STRATEGY_IDS = (
    "inside_bar_momentum_breakout_v1",
    "ema_cross_scalp_v1",
    "breakout_retest_v1",
    "pinbar_reversal_v1",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HarnessParameters(_StrictModel):
    horizons_minutes: tuple[int, ...]
    intrabar_policy: str = "stop_first"
    max_market_gap_minutes: int = Field(gt=0, le=1440)
    h1_ema_fast: int = Field(ge=2)
    h1_ema_slow: int = Field(ge=3)
    sr_feature_atr_period: int = Field(ge=2)
    regime_timeframe: str = "M15"
    regime_ema_fast: int = Field(ge=2)
    regime_ema_slow: int = Field(ge=3)
    regime_atr_period: int = Field(ge=2)
    regime_adx_period: int = Field(ge=2)
    regime_min_adx: float = Field(ge=0, le=100)
    regime_min_separation_atr: float = Field(ge=0)
    regime_slope_lookback_bars: int = Field(ge=1, le=20)
    regime_min_slow_slope_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _validate(self) -> "HarnessParameters":
        if not self.horizons_minutes or any(value <= 0 for value in self.horizons_minutes):
            raise ValueError("horizons_minutes must contain positive values")
        if tuple(sorted(set(self.horizons_minutes))) != self.horizons_minutes:
            raise ValueError("horizons_minutes must be unique and sorted")
        if self.intrabar_policy not in {"stop_first", "target_first"}:
            raise ValueError("unsupported intrabar_policy")
        if self.h1_ema_fast >= self.h1_ema_slow:
            raise ValueError("h1_ema_fast must be smaller than h1_ema_slow")
        if self.regime_timeframe not in {"M5", "M15", "H1"}:
            raise ValueError("regime_timeframe must be M5, M15 or H1")
        if self.regime_ema_fast >= self.regime_ema_slow:
            raise ValueError("regime_ema_fast must be smaller than regime_ema_slow")
        return self


class _BaseStrategyParameters(_StrictModel):
    decision_timeframe: str = "M5"
    atr_period: int = Field(ge=2)
    stop_buffer_atr: float = Field(ge=0)
    target_r: float = Field(gt=0)
    expiry_minutes: int = Field(gt=0, le=1440)

    @model_validator(mode="after")
    def _timeframe(self) -> "_BaseStrategyParameters":
        if self.decision_timeframe not in {"M5", "M15"}:
            raise ValueError("decision_timeframe must be M5 or M15")
        return self


class InsideBarParameters(_BaseStrategyParameters):
    mother_min_range_atr: float = Field(ge=0)
    mother_min_body_ratio: float = Field(ge=0, le=1)
    require_mother_direction: bool
    breakout_buffer_atr: float = Field(ge=0)


class EMACrossParameters(_BaseStrategyParameters):
    ema_trigger: int = Field(ge=2)
    ema_signal: int = Field(ge=3)
    ema_trend: int = Field(ge=4)
    stop_lookback_bars: int = Field(ge=1, le=20)

    @model_validator(mode="after")
    def _ema_order(self) -> "EMACrossParameters":
        if not (self.ema_trigger < self.ema_signal < self.ema_trend):
            raise ValueError("EMA periods must satisfy trigger < signal < trend")
        return self


class BreakoutRetestParameters(_BaseStrategyParameters):
    range_bars: int = Field(ge=2)
    breakout_search_bars: int = Field(ge=2, le=50)
    min_breakout_close_atr: float = Field(ge=0)
    retest_tolerance_atr: float = Field(ge=0)
    confirmation_buffer_atr: float = Field(ge=0)


class PinbarParameters(_BaseStrategyParameters):
    min_range_atr: float = Field(ge=0)
    max_body_ratio: float = Field(ge=0, le=1)
    min_primary_wick_ratio: float = Field(ge=0, le=1)
    max_opposite_wick_ratio: float = Field(ge=0, le=1)
    min_close_extreme_ratio: float = Field(ge=0, le=1)
    max_sr_distance_atr: float = Field(ge=0)


PARAMETER_MODELS: Mapping[str, type[_BaseStrategyParameters]] = MappingProxyType({
    "inside_bar_momentum_breakout_v1": InsideBarParameters,
    "ema_cross_scalp_v1": EMACrossParameters,
    "breakout_retest_v1": BreakoutRetestParameters,
    "pinbar_reversal_v1": PinbarParameters,
})


@dataclass(frozen=True)
class ScalpingStrategySpec:
    strategy_id: str
    version: str
    enabled: bool
    parameters: _BaseStrategyParameters
    fingerprint: str


@dataclass(frozen=True)
class ScalpingResearchConfig:
    schema_version: int
    harness: HarnessParameters
    strategies: Mapping[str, ScalpingStrategySpec]
    fingerprint: str
    source_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategies", MappingProxyType(dict(self.strategies)))

    def enabled_strategies(self) -> tuple[ScalpingStrategySpec, ...]:
        return tuple(self.strategies[strategy_id] for strategy_id in STRATEGY_IDS if self.strategies[strategy_id].enabled)


def default_scalping_config_path() -> Path:
    candidates = [
        PROJECT_ROOT / "config" / "scalping-strategies.yaml",
        Path.home() / "apps" / "forex-ai" / "current" / "config" / "scalping-strategies.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def load_scalping_research_config(path: Path | None = None) -> ScalpingResearchConfig:
    source = Path(path).expanduser() if path is not None else default_scalping_config_path()
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("scalping strategy config root must be a mapping")
    unknown_root = set(raw) - {"schema_version", "harness", "strategies"}
    if unknown_root:
        raise ValueError(f"unsupported scalping config keys: {sorted(unknown_root)}")
    schema_version = int(raw.get("schema_version", 0))
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported scalping schema_version={schema_version}")
    harness_raw = raw.get("harness")
    strategies_raw = raw.get("strategies")
    if not isinstance(harness_raw, dict) or not isinstance(strategies_raw, dict):
        raise ValueError("harness and strategies mappings are required")
    unknown = tuple(sorted(set(strategies_raw) - set(STRATEGY_IDS)))
    missing = tuple(strategy_id for strategy_id in STRATEGY_IDS if strategy_id not in strategies_raw)
    if unknown:
        raise ValueError(f"unsupported scalping strategy ids: {unknown}")
    if missing:
        raise ValueError(f"missing scalping strategy ids: {missing}")

    harness = HarnessParameters.model_validate(harness_raw)
    specs: dict[str, ScalpingStrategySpec] = {}
    canonical: dict[str, object] = {
        "schema_version": schema_version,
        "harness": harness.model_dump(mode="python"),
        "strategies": {},
    }
    for strategy_id in STRATEGY_IDS:
        item = strategies_raw[strategy_id]
        if not isinstance(item, dict):
            raise ValueError(f"strategy {strategy_id} must be a mapping")
        unknown_keys = set(item) - {"enabled", "version", "parameters"}
        if unknown_keys:
            raise ValueError(f"strategy {strategy_id} has unsupported keys: {sorted(unknown_keys)}")
        version = str(item.get("version", "")).strip()
        if not version:
            raise ValueError(f"strategy {strategy_id} version is required")
        enabled = bool(item.get("enabled", True))
        parameters_raw = item.get("parameters")
        if not isinstance(parameters_raw, dict):
            raise ValueError(f"strategy {strategy_id} parameters must be a mapping")
        parameters = PARAMETER_MODELS[strategy_id].model_validate(parameters_raw)
        item_canonical = {
            "enabled": enabled,
            "version": version,
            "parameters": parameters.model_dump(mode="python"),
        }
        config_fingerprint = fingerprint({"strategy_id": strategy_id, **item_canonical})
        specs[strategy_id] = ScalpingStrategySpec(
            strategy_id=strategy_id,
            version=version,
            enabled=enabled,
            parameters=parameters,
            fingerprint=config_fingerprint,
        )
        canonical["strategies"][strategy_id] = {**item_canonical, "fingerprint": config_fingerprint}

    return ScalpingResearchConfig(
        schema_version=schema_version,
        harness=harness,
        strategies=specs,
        fingerprint=fingerprint(canonical),
        source_path=source,
    )
