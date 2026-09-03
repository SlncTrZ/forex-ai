from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_risk_per_trade_pct: Decimal = Field(gt=0, le=Decimal("100"))
    max_total_open_risk_pct: Decimal = Field(gt=0, le=Decimal("100"))
    daily_loss_limit_pct: Decimal = Field(gt=0, le=Decimal("100"))
    weekly_loss_limit_pct: Decimal = Field(gt=0, le=Decimal("100"))
    max_active_orders: int = Field(gt=0)

    max_risk_per_trade_amount: Decimal | None = Field(default=None, gt=0)
    max_total_open_risk_amount: Decimal | None = Field(default=None, gt=0)
    daily_loss_limit_amount: Decimal | None = Field(default=None, gt=0)
    weekly_loss_limit_amount: Decimal | None = Field(default=None, gt=0)
    max_drawdown_amount: Decimal | None = Field(default=None, gt=0)

    min_risk_reward: Decimal = Field(default=Decimal("1.5"), gt=0)
    max_signal_age_seconds: int = Field(default=30, gt=0)
    max_tick_age_seconds: int = Field(default=5, gt=0)
    max_price_drift_pct: Decimal = Field(default=Decimal("0.25"), ge=0)
    max_spread_points: int = Field(default=100, ge=0)
    max_slippage_points: int = Field(default=50, ge=0)
    min_margin_reserve_pct: Decimal = Field(default=Decimal("20"), ge=0, lt=Decimal("100"))
    max_correlated_risk_pct: Decimal = Field(default=Decimal("3"), gt=0, le=Decimal("100"))
    cooldown_seconds: int = Field(default=0, ge=0)
    max_drawdown_pct: Decimal = Field(default=Decimal("10"), gt=0, le=Decimal("100"))
    conservative_fee_per_lot: Decimal = Field(default=Decimal("0"), ge=0)
    enabled: bool = True
    kill_switch: bool = False

    @model_validator(mode="after")
    def invariants(self):
        if self.max_total_open_risk_pct < self.max_risk_per_trade_pct:
            raise ValueError("max_total_open_risk_pct must be >= max_risk_per_trade_pct")
        if self.weekly_loss_limit_pct < self.daily_loss_limit_pct:
            raise ValueError("weekly_loss_limit_pct must be >= daily_loss_limit_pct")
        if self.max_total_open_risk_amount is not None and self.max_risk_per_trade_amount is not None:
            if self.max_total_open_risk_amount < self.max_risk_per_trade_amount:
                raise ValueError("max_total_open_risk_amount must be >= max_risk_per_trade_amount")
        if self.weekly_loss_limit_amount is not None and self.daily_loss_limit_amount is not None:
            if self.weekly_loss_limit_amount < self.daily_loss_limit_amount:
                raise ValueError("weekly_loss_limit_amount must be >= daily_loss_limit_amount")
        return self

    def canonical_json(self) -> str:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
