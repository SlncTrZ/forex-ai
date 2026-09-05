from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping, Sequence

from forex_ai.market.context_config import HigherTimeframeStructureConfig, TimeframeStructureConfig
from forex_ai.market.indicators import atr
from forex_ai.strategy.v1.contracts import Candle, TimeframeSnapshot


@dataclass(frozen=True)
class StructureLevel:
    """Price-independent H4/D1 structure level.

    Pivot detection/clustering is the expensive part of higher-timeframe context.
    A StructureLevel can be cached until H4/D1 closed-bar identity changes, then
    cheaply projected against each new M5 reference price.
    """

    timeframe: str
    center: float
    lower: float
    upper: float
    source_atr: float
    touches: int
    importance: float
    origins: tuple[str, ...]
    last_pivot_utc: datetime


@dataclass(frozen=True)
class StructureZone:
    timeframe: str
    role: str
    center: float
    lower: float
    upper: float
    distance_from_price: float
    distance_atr: float
    touches: int
    importance: float
    origins: tuple[str, ...]
    last_pivot_utc: datetime

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["origins"] = list(self.origins)
        payload["last_pivot_utc"] = self.last_pivot_utc.isoformat()
        return payload


@dataclass(frozen=True)
class HigherTimeframeStructure:
    status: str
    captured_at_utc: datetime
    reference_price: float
    supports: tuple[StructureZone, ...]
    resistances: tuple[StructureZone, ...]
    config_fingerprint: str
    source_timeframes: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "captured_at_utc": self.captured_at_utc.isoformat(),
            "reference_price": self.reference_price,
            "supports": [zone.to_dict() for zone in self.supports],
            "resistances": [zone.to_dict() for zone in self.resistances],
            "config_fingerprint": self.config_fingerprint,
            "source_timeframes": list(self.source_timeframes),
            "error": self.error,
            "context_only": True,
        }


def _pivot_points(
    bars: Sequence[Candle],
    config: TimeframeStructureConfig,
) -> list[tuple[float, str, datetime]]:
    left = config.pivot_left
    right = config.pivot_right
    points: list[tuple[float, str, datetime]] = []
    for index in range(left, len(bars) - right):
        bar = bars[index]
        left_bars = bars[index - left:index]
        right_bars = bars[index + 1:index + right + 1]
        if bar.high > max(item.high for item in left_bars) and bar.high >= max(item.high for item in right_bars):
            points.append((bar.high, "pivot_high", bar.time_utc))
        if bar.low < min(item.low for item in left_bars) and bar.low <= min(item.low for item in right_bars):
            points.append((bar.low, "pivot_low", bar.time_utc))

    # Visible extrema are descriptive context, never trade triggers. They are
    # intentionally causal because only already-closed bars are supplied here.
    if bars:
        high_bar = max(bars, key=lambda item: item.high)
        low_bar = min(bars, key=lambda item: item.low)
        points.append((high_bar.high, "range_high", high_bar.time_utc))
        points.append((low_bar.low, "range_low", low_bar.time_utc))
    return points


def _cluster_levels(
    timeframe: str,
    bars: Sequence[Candle],
    config: TimeframeStructureConfig,
) -> list[StructureLevel]:
    bars = bars[-config.history_bars:]
    atr_value = atr(bars, config.atr_period)
    if atr_value <= 0:
        return []
    points = sorted(_pivot_points(bars, config), key=lambda item: item[0])
    if not points:
        return []

    merge_distance = atr_value * config.cluster_distance_atr
    clusters: list[list[tuple[float, str, datetime]]] = []
    for point in points:
        if not clusters:
            clusters.append([point])
            continue
        center = sum(item[0] for item in clusters[-1]) / len(clusters[-1])
        if abs(point[0] - center) <= merge_distance:
            clusters[-1].append(point)
        else:
            clusters.append([point])

    half_width = atr_value * config.zone_half_width_atr
    levels: list[StructureLevel] = []
    for cluster in clusters:
        center = sum(item[0] for item in cluster) / len(cluster)
        origins = tuple(sorted({item[1] for item in cluster}))
        last_pivot = max(item[2] for item in cluster)
        touches = len(cluster)
        levels.append(
            StructureLevel(
                timeframe=timeframe,
                center=center,
                lower=center - half_width,
                upper=center + half_width,
                source_atr=atr_value,
                touches=touches,
                importance=touches * config.timeframe_weight,
                origins=origins,
                last_pivot_utc=last_pivot,
            )
        )
    return levels


def extract_higher_timeframe_levels(
    *,
    timeframes: Mapping[str, TimeframeSnapshot],
    config: HigherTimeframeStructureConfig,
) -> tuple[tuple[StructureLevel, ...], tuple[str, ...]]:
    """Extract price-independent H4/D1 levels from closed bars."""
    if not config.enabled:
        return (), ()
    levels: list[StructureLevel] = []
    used: list[str] = []
    for timeframe_name, timeframe_config in config.timeframes.items():
        snapshot = timeframes.get(timeframe_name)
        if snapshot is None or not snapshot.closed_bars:
            continue
        used.append(timeframe_name)
        levels.extend(_cluster_levels(timeframe_name, snapshot.closed_bars, timeframe_config))
    return tuple(levels), tuple(used)


def project_higher_timeframe_structure(
    *,
    levels: Sequence[StructureLevel],
    source_timeframes: Sequence[str],
    reference_price: float,
    captured_at_utc: datetime,
    config: HigherTimeframeStructureConfig,
    config_fingerprint: str,
) -> HigherTimeframeStructure:
    """Project cached levels against a current price without re-clustering."""
    if not config.enabled:
        return HigherTimeframeStructure(
            status="DISABLED",
            captured_at_utc=captured_at_utc,
            reference_price=reference_price,
            supports=(),
            resistances=(),
            config_fingerprint=config_fingerprint,
            source_timeframes=(),
        )

    zones: list[StructureZone] = []
    for level in levels:
        distance = level.center - reference_price
        zones.append(
            StructureZone(
                timeframe=level.timeframe,
                role="support" if level.center <= reference_price else "resistance",
                center=level.center,
                lower=level.lower,
                upper=level.upper,
                distance_from_price=distance,
                distance_atr=distance / level.source_atr,
                touches=level.touches,
                importance=level.importance,
                origins=level.origins,
                last_pivot_utc=level.last_pivot_utc,
            )
        )

    supports = sorted(
        (zone for zone in zones if zone.role == "support"),
        key=lambda zone: (-zone.importance, abs(zone.distance_from_price), -zone.last_pivot_utc.timestamp()),
    )[:config.max_support_levels]
    resistances = sorted(
        (zone for zone in zones if zone.role == "resistance"),
        key=lambda zone: (-zone.importance, abs(zone.distance_from_price), -zone.last_pivot_utc.timestamp()),
    )[:config.max_resistance_levels]
    used = tuple(source_timeframes)
    status = "READY" if used else "UNAVAILABLE"
    return HigherTimeframeStructure(
        status=status,
        captured_at_utc=captured_at_utc,
        reference_price=reference_price,
        supports=tuple(supports),
        resistances=tuple(resistances),
        config_fingerprint=config_fingerprint,
        source_timeframes=used,
        error=None if used else "NO_HIGHER_TIMEFRAME_DATA",
    )


def build_higher_timeframe_structure(
    *,
    timeframes: Mapping[str, TimeframeSnapshot],
    reference_price: float,
    captured_at_utc: datetime,
    config: HigherTimeframeStructureConfig,
    config_fingerprint: str,
) -> HigherTimeframeStructure:
    levels, used = extract_higher_timeframe_levels(timeframes=timeframes, config=config)
    return project_higher_timeframe_structure(
        levels=levels,
        source_timeframes=used,
        reference_price=reference_price,
        captured_at_utc=captured_at_utc,
        config=config,
        config_fingerprint=config_fingerprint,
    )
