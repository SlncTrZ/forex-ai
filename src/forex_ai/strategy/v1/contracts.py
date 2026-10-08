"""Contracts bất biến strategy v1 — snapshot thị trường, cấu hình, envelope ứng viên.
Updated: 2026-10-08 20:05
"""
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
    """Băm SHA-256 của giá trị sau chuẩn hoá chính tắc (datetime về UTC, Mapping sắp xếp theo key).

    Params:
        value: Giá trị bất kỳ (datetime/Mapping/sequence hoặc object có `to_dict` được chuẩn hoá đệ quy).
    Returns:
        Chuỗi hex SHA-256 của JSON chuẩn hoá (`sort_keys`, không khoảng trắng).
    """
    raw = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candle:
    """Nến OHLCV bất biến; yêu cầu `time_utc` có tzinfo, giá dương hữu hạn, OHLC nhất quán, volume không âm."""

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
        """Returns: dict các thuộc tính của nến."""
        return vars(self)


@dataclass(frozen=True)
class TimeframeSnapshot:
    """Ảnh một timeframe: tuple nến đã đóng tăng dần nghiêm ngặt, không trùng; nến hiện tại (nếu có) phải mới hơn nến đóng cuối."""

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
        """Dựng snapshot từ sequence nến.

        Params:
            timeframe: Mã timeframe (bắt buộc khác rỗng).
            bars: Sequence nến đã đóng (được ép thành tuple, phải tăng dần nghiêm ngặt).
            current_bar: Nến đang chạy, mặc định None.
        Returns:
            TimeframeSnapshot tương ứng.
        """
        return cls(timeframe, tuple(bars), current_bar)

    def to_dict(self) -> dict[str, Any]:
        """Returns: dict gồm `timeframe`, `closed_bars`, `current_bar`."""
        return {"timeframe": self.timeframe, "closed_bars": self.closed_bars, "current_bar": self.current_bar}


@dataclass(frozen=True)
class MarketSnapshot:
    """Ảnh thị trường bất biến tại thời điểm chụp: giá bid/ask, chi phí, các timeframe; `timeframes`/`metadata`/`context` được khoá MappingProxyType."""

    symbol: str
    captured_at_utc: datetime
    market_time_msc: int
    bid: float
    ask: float
    timeframes: Mapping[str, TimeframeSnapshot]
    spread_cost: float = 0.0
    commission_cost: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)

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
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))

    @property
    def fingerprint(self) -> str:
        """Returns: SHA-256 của toàn bộ `to_dict()` (gồm cả `context` và nến hiện tại)."""
        return fingerprint(self.to_dict())

    @property
    def decision_fingerprint(self) -> str:
        """Returns: SHA-256 của phần dữ liệu dùng ra quyết định (loại `context` và nến hiện tại, chỉ giữ nến đã đóng)."""
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
        """Returns: dict đầy đủ các trường snapshot (symbol, giá, timeframes, chi phí, metadata, context)."""
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
            "context": self.context,
        }


@dataclass(frozen=True)
class StrategyVersion:
    """Định danh phiên bản chiến lược: `strategy_id` + `version`."""

    strategy_id: str
    version: str


@dataclass(frozen=True)
class StrategyConfig:
    """Cấu hình chiến lược bất biến: phiên bản, tham số (khoá MappingProxyType), lớp instrument."""

    version: StrategyVersion
    parameters: Mapping[str, Any]
    instrument_class: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def fingerprint(self) -> str:
        """Returns: SHA-256 của version, tham số và lớp instrument."""
        return fingerprint({"version": vars(self.version), "parameters": self.parameters, "instrument_class": self.instrument_class})


@dataclass(frozen=True)
class Invalidation:
    """Điểm vô hiệu setup: loại (`kind`), giá kích hoạt và lý do."""

    kind: str
    price: float
    reason: str


@dataclass(frozen=True)
class DecisionEvidence:
    """Bằng chứng ra quyết định: tuple mã lý do và mapping giá trị (khoá MappingProxyType)."""

    reason_codes: tuple[str, ...]
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))

    @property
    def evidence_hash(self) -> str:
        """Returns: SHA-256 của `reason_codes` và `values`."""
        return fingerprint({"reason_codes": self.reason_codes, "values": self.values})


@dataclass(frozen=True)
class CandidateEnvelope:
    """Phong bì ứng viên giao dịch: định danh ổn định theo cơ hội, phía BUY/SELL, entry/SL/TP, mốc thời gian tz-aware và các fingerprint liên kết."""

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
    opportunity_key: str = ""
    strategy_config_fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("invalid side")
        if self.generated_at_utc.tzinfo is None or self.expires_at_utc.tzinfo is None:
            raise ValueError("timezone-aware timestamps required")


@dataclass(frozen=True)
class StrategyResult:
    """Kết quả chiến lược: ứng viên (None nếu không có setup), điểm vô hiệu, bằng chứng và mã lý do no-setup."""

    candidate: CandidateEnvelope | None
    invalidation: Invalidation | None
    evidence: DecisionEvidence
    no_setup_reason_codes: tuple[str, ...] = ()


def build_candidate(*, snapshot: MarketSnapshot, config: StrategyConfig, side: str, entry: float, stop_loss: float,
                    take_profit: float, generated_at_utc: datetime, expires_at_utc: datetime,
                    evidence: DecisionEvidence, decision_timeframe: str = "M15") -> CandidateEnvelope:
    """Dựng envelope ứng viên với `candidate_id` ổn định theo cơ hội (retry/crash không tạo ứng viên trùng).

    Params:
        snapshot: Ảnh thị trường nguồn (lấy symbol, market_time_msc, decision_fingerprint).
        config: Cấu hình chiến lược (lấy id/version và fingerprint cấu hình).
        side: Phía lệnh, "BUY" hoặc "SELL".
        entry: Giá vào tham chiếu.
        stop_loss: Giá dừng lỗ.
        take_profit: Giá chốt lời.
        generated_at_utc: Thời điểm sinh (chuẩn hoá về UTC).
        expires_at_utc: Thời điểm hết hạn (chuẩn hoá về UTC).
        evidence: Bằng chứng quyết định (lấy `evidence_hash`).
        decision_timeframe: Timeframe chốt cơ hội, mặc định "M15" (lấy nến đóng cuối làm mốc; thiếu thì dùng `generated_at_utc`).
    Returns:
        CandidateEnvelope hoàn chỉnh với `opportunity_key`, `correlation_id` dạng "candidate-<id>".
    """
    decision_tf = snapshot.timeframes.get(decision_timeframe)
    decision_bar_time = (
        decision_tf.closed_bars[-1].time_utc
        if decision_tf is not None and decision_tf.closed_bars
        else generated_at_utc.astimezone(timezone.utc)
    )
    opportunity_key = fingerprint({
        "strategy_id": config.version.strategy_id,
        "strategy_version": config.version.version,
        "symbol": snapshot.symbol,
        "decision_timeframe": decision_timeframe,
        "closed_decision_bar_time": decision_bar_time,
    })
    # Candidate identity is stable for one business opportunity. Volatile tick,
    # capture time and evidence remain in the payload/fingerprints but cannot
    # manufacture a second tradable candidate on crash/retry.
    candidate_id = fingerprint({"opportunity_key": opportunity_key, "side": side})[:32]
    return CandidateEnvelope(
        candidate_id, f"candidate-{candidate_id}", config.version.strategy_id, config.version.version,
        snapshot.symbol, side, entry, stop_loss, take_profit, generated_at_utc.astimezone(timezone.utc),
        snapshot.market_time_msc, expires_at_utc.astimezone(timezone.utc), evidence.evidence_hash,
        snapshot.decision_fingerprint, opportunity_key, config.fingerprint,
    )
