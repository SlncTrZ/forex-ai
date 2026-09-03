from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return dt.astimezone(timezone.utc)


def canonical_fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AccountSnapshot(FrozenModel):
    login: int
    server: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    balance: float
    equity: float
    margin: float = 0.0
    margin_free: float = 0.0
    leverage: int = Field(gt=0)
    captured_at_utc: datetime

    @field_validator("balance", "equity", "margin", "margin_free")
    @classmethod
    def finite_money(cls, value: float, info):
        return _finite(value, info.field_name)

    @field_validator("captured_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @property
    def identity_fingerprint(self) -> str:
        return canonical_fingerprint({"login": self.login, "server": self.server, "currency": self.currency})


class SymbolContract(FrozenModel):
    symbol: str = Field(min_length=1)
    digits: int = Field(ge=0)
    point: float = Field(gt=0)
    trade_contract_size: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    trade_stops_level: int = Field(ge=0, default=0)
    trade_freeze_level: int = Field(ge=0, default=0)
    trade_mode: int | None = None
    order_mode: int | None = None
    filling_mode: int | None = None
    trade_allowed: bool = True
    market_orders_allowed: bool = True
    session_open: bool = True
    currency_base: str | None = None
    currency_profit: str | None = None
    currency_margin: str | None = None

    @field_validator("point", "trade_contract_size", "volume_min", "volume_max", "volume_step")
    @classmethod
    def finite_positive(cls, value: float, info):
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def check_volume_rules(self):
        if self.volume_max < self.volume_min:
            raise ValueError("volume_max must be >= volume_min")
        if self.volume_step > self.volume_max:
            raise ValueError("volume_step must not exceed volume_max")
        return self

    @property
    def contract_fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"trade_allowed", "market_orders_allowed", "session_open"})
        return canonical_fingerprint(payload)


class TickSnapshot(FrozenModel):
    symbol: str = Field(min_length=1)
    bid: float
    ask: float
    time_msc: int = Field(gt=0)
    captured_at_utc: datetime

    @field_validator("bid", "ask")
    @classmethod
    def finite_price(cls, value: float, info):
        return _finite(value, info.field_name)

    @field_validator("captured_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def check_spread(self):
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("invalid bid/ask")
        return self


class Bar(FrozenModel):
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = Field(ge=0)

    @field_validator("time_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_validator("open", "high", "low", "close")
    @classmethod
    def finite_price(cls, value: float, info):
        return _finite(value, info.field_name)

    @model_validator(mode="after")
    def check_ohlc(self):
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.high < self.low:
            raise ValueError("invalid OHLC ordering")
        return self


class BarSeries(FrozenModel):
    symbol: str = Field(min_length=1)
    timeframe_seconds: int = Field(gt=0)
    closed_bars: tuple[Bar, ...] = ()
    current_bar: Bar | None = None

    @model_validator(mode="after")
    def check_order(self):
        bars = list(self.closed_bars)
        if len({b.time_utc for b in bars}) != len(bars):
            raise ValueError("duplicate bars")
        if bars != sorted(bars, key=lambda b: b.time_utc):
            raise ValueError("bars must be ordered")
        if self.current_bar and bars and self.current_bar.time_utc <= bars[-1].time_utc:
            raise ValueError("current bar must be newer than closed bars")
        return self


class BrokerOrder(FrozenModel):
    ticket: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    volume_initial: float = Field(ge=0)
    volume_current: float = Field(ge=0)
    price_open: float = Field(ge=0)
    sl: float = Field(ge=0, default=0)
    tp: float = Field(ge=0, default=0)
    state: int | None = None
    magic: int | None = None
    comment: str = ""


class BrokerDeal(FrozenModel):
    ticket: int = Field(gt=0)
    order: int = Field(ge=0)
    position_id: int = Field(ge=0)
    symbol: str = Field(min_length=1)
    volume: float = Field(gt=0)
    price: float = Field(gt=0)
    profit: float = 0.0
    time_msc: int = Field(gt=0)


class BrokerPosition(FrozenModel):
    ticket: int = Field(gt=0)
    symbol: str = Field(min_length=1)
    side: str
    volume: float = Field(gt=0)
    price_open: float = Field(gt=0)
    price_current: float = Field(gt=0)
    sl: float = Field(ge=0, default=0)
    tp: float = Field(ge=0, default=0)
    profit: float = 0.0
    magic: int | None = None
    comment: str = ""

    @field_validator("side")
    @classmethod
    def validate_side(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"BUY", "SELL"}:
            raise ValueError("position side must be BUY or SELL")
        return normalized


class BrokerState(FrozenModel):
    account: AccountSnapshot
    contracts: tuple[SymbolContract, ...]
    ticks: tuple[TickSnapshot, ...]
    positions: tuple[BrokerPosition, ...] = ()
    pending_orders: tuple[BrokerOrder, ...] = ()
    recent_orders: tuple[BrokerOrder, ...] = ()
    recent_deals: tuple[BrokerDeal, ...] = ()
    reconciled_at_utc: datetime

    @field_validator("reconciled_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)


class SafetySnapshot(FrozenModel):
    account_fingerprint: str = Field(min_length=64, max_length=64)
    contracts_fingerprint: str = Field(min_length=64, max_length=64)
    reconciled: bool
    blocking_reasons: tuple[str, ...] = ()
    captured_at_utc: datetime

    @field_validator("captured_at_utc")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return _utc(value)

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.model_dump(mode="json"))
