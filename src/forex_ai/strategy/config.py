"""Strategy Config — schema, tham số và nạp snapshot cấu hình chiến lược.
Wing: strategy | Topic: config | Updated: 2026-10-08 20:16

Định nghĩa SCHEMA_VERSION, danh sách strategy ID, model Pydantic cho parameters,
snapshot dataclass và hàm đọc/biên dịch YAML kèm fallback last-good.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from forex_ai.config import PROJECT_ROOT
from forex_ai.strategy.v1.contracts import StrategyConfig, StrategyVersion, fingerprint

SCHEMA_VERSION = 1
PRODUCTION_STRATEGY_IDS = (
    "inside_bar_momentum_breakout_v1",
    "breakout_retest_v1",
    "trend_pullback_v1",
    "volatility_breakout_v1",
)
EXPLORATION_STRATEGY_IDS = ("exploration_trend_v1", "exploration_breakout_v1")
ALL_STRATEGY_IDS = PRODUCTION_STRATEGY_IDS + EXPLORATION_STRATEGY_IDS


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrendParameters(_StrictModel):
    """Tham số chiến lược trend_pullback_v1.

    Ràng buộc ema_fast < ema_slow; kiểm tra biên EMA/ATR/target/expiry.
    """
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)
    atr_period: int = Field(ge=2)
    pullback_atr: float = Field(ge=0)
    volatility_buffer_atr: float = Field(ge=0)
    structure_lookback_bars: int = Field(ge=2)
    target_r: float = Field(gt=0)
    expiry_minutes: int = Field(gt=0)
    m5_confirm: bool = False

    @model_validator(mode="after")
    def _ema_order(self) -> "TrendParameters":
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be smaller than ema_slow")
        return self


class InsideBarMomentumParameters(_StrictModel):
    """Tham số inside_bar_momentum_breakout_v1.

    Yêu cầu decision_timeframe luôn là M5; kiểm tra biên ATR/body-ratio/target/expiry.
    """
    decision_timeframe: str = "M5"
    atr_period: int = Field(ge=2)
    mother_min_range_atr: float = Field(ge=0)
    mother_min_body_ratio: float = Field(ge=0, le=1)
    require_mother_direction: bool = True
    breakout_buffer_atr: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)
    target_r: float = Field(gt=0)
    expiry_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def _timeframe(self) -> "InsideBarMomentumParameters":
        if self.decision_timeframe != "M5":
            raise ValueError("inside_bar_momentum_breakout_v1 decision_timeframe must be M5")
        return self


class BreakoutRetestParameters(_StrictModel):
    """Tham số breakout_retest_v1.

    Yêu cầu decision_timeframe luôn là M5; kiểm tra biên range/ATR/buffer/target/expiry.
    """
    decision_timeframe: str = "M5"
    range_bars: int = Field(ge=2)
    atr_period: int = Field(ge=2)
    breakout_search_bars: int = Field(ge=2)
    min_breakout_close_atr: float = Field(ge=0)
    retest_tolerance_atr: float = Field(ge=0)
    confirmation_buffer_atr: float = Field(ge=0)
    stop_buffer_atr: float = Field(ge=0)
    target_r: float = Field(gt=0)
    expiry_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def _timeframe(self) -> "BreakoutRetestParameters":
        if self.decision_timeframe != "M5":
            raise ValueError("breakout_retest_v1 decision_timeframe must be M5")
        return self


class ExplorationTrendParameters(_StrictModel):
    """Tham số exploration_trend_v1.

    Ràng buộc ema_fast < ema_slow; thêm probe_distance_atr cho lệnh thăm dò.
    """
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)
    atr_period: int = Field(ge=2)
    pullback_atr: float = Field(ge=0)
    volatility_buffer_atr: float = Field(ge=0)
    structure_lookback_bars: int = Field(ge=2)
    target_r: float = Field(gt=0)
    expiry_minutes: int = Field(gt=0)
    probe_distance_atr: float = Field(gt=0)

    @model_validator(mode="after")
    def _ema_order(self) -> "ExplorationTrendParameters":
        if self.ema_fast >= self.ema_slow:
            raise ValueError("ema_fast must be smaller than ema_slow")
        return self


class BreakoutParameters(_StrictModel):
    """Tham số volatility_breakout_v1 và exploration_breakout_v1.

    Ràng buộc trend_ema_fast < trend_ema_slow; kiểm tra biên range/ATR/efficiency/extension/cost.
    """
    range_bars: int = Field(ge=2)
    atr_period: int = Field(ge=2)
    trend_ema_fast: int = Field(ge=2)
    trend_ema_slow: int = Field(ge=3)
    efficiency_window: int = Field(ge=2)
    min_expansion: float = Field(ge=0)
    min_efficiency: float = Field(ge=0, le=1)
    max_extension_atr: float = Field(gt=0)
    stop_buffer_atr: float = Field(ge=0)
    target_r: float = Field(gt=0)
    expiry_minutes: int = Field(gt=0)
    max_cost_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def _ema_order(self) -> "BreakoutParameters":
        if self.trend_ema_fast >= self.trend_ema_slow:
            raise ValueError("trend_ema_fast must be smaller than trend_ema_slow")
        return self


PARAMETER_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType({
    "inside_bar_momentum_breakout_v1": InsideBarMomentumParameters,
    "breakout_retest_v1": BreakoutRetestParameters,
    "trend_pullback_v1": TrendParameters,
    "volatility_breakout_v1": BreakoutParameters,
    "exploration_trend_v1": ExplorationTrendParameters,
    "exploration_breakout_v1": BreakoutParameters,
})


@dataclass(frozen=True)
class StrategySpec:
    """Đặc tả một chiến lược: cờ enabled và StrategyConfig đã biên dịch."""
    enabled: bool
    config: StrategyConfig


@dataclass(frozen=True)
class StrategyConfigSnapshot:
    """Snapshot cấu hình đã biên dịch: schema_version, strategies, fingerprint, source_path."""
    schema_version: int
    strategies: Mapping[str, StrategySpec]
    fingerprint: str
    source_path: Path
    loaded_from_last_good: bool = False
    rejected_error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategies", MappingProxyType(dict(self.strategies)))

    def config_for(self, strategy_id: str) -> StrategyConfig:
        """Trả về StrategyConfig của strategy_id.

        Params:
            strategy_id: mã chiến lược cần lấy.
        Returns:
            StrategyConfig đã biên dịch.
        Raises:
            KeyError: khi strategy_id không có trong snapshot.
        """
        try:
            return self.strategies[strategy_id].config
        except KeyError as exc:
            raise KeyError(f"strategy config missing: {strategy_id}") from exc

    def enabled(self, strategy_id: str) -> bool:
        """Kiểm tra cờ enabled của strategy_id.

        Params:
            strategy_id: mã chiến lược cần kiểm tra.
        Returns:
            True nếu được bật, False nếu tắt.
        """
        return bool(self.strategies[strategy_id].enabled)

    def fingerprint_for(self, strategy_ids: tuple[str, ...]) -> str:
        """Tính fingerprint cho tập strategy_ids từ cờ enabled và config_fingerprint.

        Params:
            strategy_ids: tuple mã chiến lược cần tổng hợp.
        Returns:
            Chuỗi fingerprint của tập đã chọn.
        """
        return fingerprint({
            strategy_id: {
                "enabled": self.strategies[strategy_id].enabled,
                "config_fingerprint": self.strategies[strategy_id].config.fingerprint,
            }
            for strategy_id in strategy_ids
        })

    @property
    def production_fingerprint(self) -> str:
        """Fingerprint của nhóm production (PRODUCTION_STRATEGY_IDS).

        Returns:
            Chuỗi fingerprint của các chiến lược production.
        """
        return self.fingerprint_for(PRODUCTION_STRATEGY_IDS)


def _bundled_path() -> Path:
    candidates = [
        PROJECT_ROOT / "config" / "strategy.yaml",
        Path.home() / "apps" / "forex-ai" / "current" / "config" / "strategy.yaml",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def runtime_strategy_path() -> Path:
    """Trả về đường dẫn strategy.yaml runtime.

    Returns:
        Path từ env FOREX_AI_STRATEGY_CONFIG nếu đặt, ngược lại ~/.config/forex-ai/strategy.yaml.
    """
    explicit = os.getenv("FOREX_AI_STRATEGY_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".config" / "forex-ai" / "strategy.yaml"


def last_good_path(active_path: Path | None = None) -> Path:
    """Trả về đường dẫn file last-good cạnh file active.

    Params:
        active_path: đường dẫn active; mặc định dùng runtime_strategy_path().
    Returns:
        Path strategy.last-good.yaml cùng thư mục với active.
    """
    active = active_path or runtime_strategy_path()
    return active.with_name("strategy.last-good.yaml")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    if not isinstance(raw, dict):
        raise ValueError("strategy config root must be a mapping")
    return raw


def _compile(raw: Mapping[str, Any], *, source_path: Path, loaded_from_last_good: bool = False,
             rejected_error: str | None = None) -> StrategyConfigSnapshot:
    schema_version = int(raw.get("schema_version", 0))
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported strategy config schema_version={schema_version}")
    raw_strategies = raw.get("strategies")
    if not isinstance(raw_strategies, Mapping):
        raise ValueError("strategies mapping is required")

    unknown = tuple(sorted(set(raw_strategies) - set(ALL_STRATEGY_IDS)))
    if unknown:
        raise ValueError(f"unsupported strategy ids: {unknown}")
    missing = tuple(strategy_id for strategy_id in ALL_STRATEGY_IDS if strategy_id not in raw_strategies)
    if missing:
        raise ValueError(f"missing strategy ids: {missing}")

    compiled: dict[str, StrategySpec] = {}
    canonical: dict[str, Any] = {"schema_version": schema_version, "strategies": {}}
    for strategy_id in ALL_STRATEGY_IDS:
        item = raw_strategies[strategy_id]
        if not isinstance(item, Mapping):
            raise ValueError(f"strategy {strategy_id} must be a mapping")
        unknown_keys = set(item) - {"enabled", "version", "parameters"}
        if unknown_keys:
            raise ValueError(f"strategy {strategy_id} has unsupported keys: {sorted(unknown_keys)}")
        version = str(item.get("version", "")).strip()
        if not version:
            raise ValueError(f"strategy {strategy_id} version is required")
        enabled = bool(item.get("enabled", True))
        parameters = item.get("parameters")
        if not isinstance(parameters, Mapping):
            raise ValueError(f"strategy {strategy_id} parameters must be a mapping")
        model = PARAMETER_MODELS[strategy_id].model_validate(dict(parameters))
        params = model.model_dump(mode="python")
        config = StrategyConfig(StrategyVersion(strategy_id, version), params)
        compiled[strategy_id] = StrategySpec(enabled=enabled, config=config)
        canonical["strategies"][strategy_id] = {
            "enabled": enabled,
            "version": version,
            "parameters": params,
            "config_fingerprint": config.fingerprint,
        }

    return StrategyConfigSnapshot(
        schema_version=schema_version,
        strategies=compiled,
        fingerprint=fingerprint(canonical),
        source_path=source_path,
        loaded_from_last_good=loaded_from_last_good,
        rejected_error=rejected_error,
    )


def load_strategy_snapshot(path: Path | None = None, *, allow_last_good: bool | None = None) -> StrategyConfigSnapshot:
    """Nạp và biên dịch snapshot từ YAML, tự copy bundled và fallback last-good khi lỗi.

    Params:
        path: đường dẫn YAML chỉ định; None dùng runtime_strategy_path().
        allow_last_good: cho phép fallback/sync last-good; mặc định True khi path None.
    Returns:
        StrategyConfigSnapshot đã biên dịch.
    Raises:
        FileNotFoundError: khi thiếu file explicit hoặc thiếu cả active lẫn bundled.
        ValueError: khi config lỗi và không có last-good hợp lệ.
    """
    explicit = path is not None
    active = Path(path).expanduser() if path is not None else runtime_strategy_path()
    if allow_last_good is None:
        allow_last_good = not explicit

    if not active.is_file():
        if explicit:
            raise FileNotFoundError(active)
        bundled = _bundled_path()
        if not bundled.is_file():
            raise FileNotFoundError(f"strategy config missing: active={active} bundled={bundled}")
        active.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, active)

    try:
        snapshot = _compile(_read_yaml(active), source_path=active)
    except Exception as exc:
        if not allow_last_good:
            raise
        fallback = last_good_path(active)
        if not fallback.is_file():
            raise ValueError(f"strategy config invalid and no last-known-good snapshot exists: {exc}") from exc
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


@lru_cache(maxsize=1)
def bundled_strategy_snapshot() -> StrategyConfigSnapshot:
    """Nạp snapshot từ file bundled kèm cache.

    Returns:
        StrategyConfigSnapshot của bundled, không dùng fallback last-good.
    """
    return load_strategy_snapshot(_bundled_path(), allow_last_good=False)


def bundled_strategy_config(strategy_id: str) -> StrategyConfig:
    """Lấy StrategyConfig bundled của một chiến lược.

    Params:
        strategy_id: mã chiến lược cần lấy.
    Returns:
        StrategyConfig từ bundled snapshot.
    """
    return bundled_strategy_snapshot().config_for(strategy_id)


def required_closed_bars(snapshot: StrategyConfigSnapshot, *, production_only: bool = True) -> int:
    """Tính số nến đóng tối thiểu từ parameters của các chiến lược đang bật.

    Params:
        snapshot: snapshot đã biên dịch.
        production_only: True chỉ xét production, False xét tất cả.
    Returns:
        Số nến đóng tối thiểu, tối thiểu là 2.
    """
    strategy_ids = PRODUCTION_STRATEGY_IDS if production_only else ALL_STRATEGY_IDS
    required = 2
    for strategy_id in strategy_ids:
        spec = snapshot.strategies[strategy_id]
        if not spec.enabled:
            continue
        p = spec.config.parameters
        if strategy_id in {"trend_pullback_v1", "exploration_trend_v1"}:
            required = max(
                required,
                int(p["ema_slow"]),
                int(p["atr_period"]) + 1,
                int(p["structure_lookback_bars"]) + 1,
            )
        elif strategy_id == "inside_bar_momentum_breakout_v1":
            required = max(required, int(p["atr_period"]) + 1, 3)
        elif strategy_id == "breakout_retest_v1":
            required = max(
                required,
                int(p["range_bars"]) + int(p["breakout_search_bars"]) + 2,
                int(p["atr_period"]) + 2,
            )
        else:
            required = max(
                required,
                int(p["range_bars"]) + 2,
                int(p["atr_period"]) + 2,
                int(p["trend_ema_slow"]) + 1,
                int(p["efficiency_window"]) + 2,
            )
    return required


def required_raw_bars(snapshot: StrategyConfigSnapshot, *, production_only: bool = True) -> int:
    """Tính số nến thô cần fetch, bằng required_closed_bars cộng 1 nến đang hình thành.

    Params:
        snapshot: snapshot đã biên dịch.
        production_only: True chỉ xét production, False xét tất cả.
    Returns:
        Số nến thô cần lấy.
    """
    # MT5 snapshot assembly removes the currently-forming bar. Fetch one extra
    # raw bar so `required_closed_bars()` remain available to the evaluator.
    return required_closed_bars(snapshot, production_only=production_only) + 1
