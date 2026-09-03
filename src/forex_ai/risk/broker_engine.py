from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Callable

from forex_ai.mt5.contracts import AccountSnapshot, SafetySnapshot, SymbolContract, TickSnapshot
from forex_ai.risk.profile import RiskProfile

D = Decimal


@dataclass(frozen=True)
class CandidateInput:
    candidate_id: str
    symbol: str
    side: str
    reference_entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    expires_at_utc: datetime
    age_seconds: Decimal = D("0")

    def __post_init__(self) -> None:
        if self.expires_at_utc.tzinfo is None:
            raise ValueError("expires_at_utc must be timezone-aware")


@dataclass(frozen=True)
class ActiveExposure:
    intent_id: str
    risk_amount: Decimal
    correlation_group: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id is required")
        if self.risk_amount < 0:
            raise ValueError("risk_amount must be >= 0")


@dataclass(frozen=True)
class RiskContext:
    exposures: tuple[ActiveExposure, ...] = ()
    proposed_correlation_group: str | None = None
    tick_age_seconds: Decimal = D("0")
    expected_slippage_points: Decimal = D("0")
    daily_realized_loss_amount: Decimal = D("0")
    weekly_realized_loss_amount: Decimal = D("0")
    drawdown_amount: Decimal = D("0")
    daily_reference_equity: Decimal | None = None
    weekly_reference_equity: Decimal | None = None
    daily_net_cash_flow: Decimal = D("0")
    weekly_net_cash_flow: Decimal = D("0")
    cooldown_active: bool = False

    @property
    def active_orders(self) -> int:
        return len({item.intent_id for item in self.exposures})

    @property
    def total_open_risk_amount(self) -> Decimal:
        return sum((item.risk_amount for item in self.exposures), D("0"))

    @property
    def correlated_open_risk_amount(self) -> Decimal:
        if self.proposed_correlation_group is None:
            return D("0")
        return sum(
            (item.risk_amount for item in self.exposures if item.correlation_group == self.proposed_correlation_group),
            D("0"),
        )


@dataclass(frozen=True)
class BrokerRiskResult:
    candidate_id: str
    approved: bool
    reason_codes: tuple[str, ...]
    normalized_symbol: str
    normalized_volume: Decimal
    executable_entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    projected_loss_account_currency: Decimal
    margin_required: Decimal
    risk_profile_fingerprint: str
    safety_snapshot_fingerprint: str
    expires_at_utc: datetime


ProfitCalculator = Callable[[str, str, Decimal, Decimal, Decimal], Decimal]
MarginCalculator = Callable[[str, str, Decimal, Decimal], Decimal]


class BrokerAwareRiskEngine:
    def __init__(self, profile: RiskProfile):
        self.profile = profile

    @staticmethod
    def _pct_amount(reference: Decimal, pct: Decimal) -> Decimal:
        return max(D("0"), reference) * pct / D("100")

    @staticmethod
    def _bounded_budget(percent_budget: Decimal, absolute_budget: Decimal | None) -> Decimal:
        return percent_budget if absolute_budget is None else min(percent_budget, absolute_budget)

    @staticmethod
    def _floor_volume(raw: Decimal, contract: SymbolContract) -> Decimal:
        minimum = D(str(contract.volume_min))
        maximum = D(str(contract.volume_max))
        step = D(str(contract.volume_step))
        if raw < minimum:
            return D("0")
        capped = min(raw, maximum)
        steps = ((capped - minimum) / step).to_integral_value(rounding=ROUND_FLOOR)
        return minimum + steps * step

    def evaluate(
        self,
        candidate: CandidateInput,
        *,
        account: AccountSnapshot,
        contract: SymbolContract,
        tick: TickSnapshot,
        safety: SafetySnapshot,
        context: RiskContext,
        calc_profit: ProfitCalculator,
        calc_margin: MarginCalculator,
        now_utc: datetime,
    ) -> BrokerRiskResult:
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        now_utc = now_utc.astimezone(timezone.utc)

        reasons: list[str] = []
        p = self.profile
        equity = D(str(account.equity))
        bid = D(str(tick.bid))
        ask = D(str(tick.ask))
        point = D(str(contract.point))

        if not p.enabled:
            reasons.append("RISK_PROFILE_DISABLED")
        if p.kill_switch:
            reasons.append("KILL_SWITCH_ACTIVE")
        if not safety.reconciled or safety.blocking_reasons:
            reasons.append("SAFETY_STATE_BLOCKED")
        if candidate.symbol != contract.symbol or candidate.symbol != tick.symbol:
            reasons.append("SYMBOL_MISMATCH")
        if candidate.side not in {"BUY", "SELL"}:
            reasons.append("INVALID_SIDE")
        if candidate.expires_at_utc.astimezone(timezone.utc) <= now_utc:
            reasons.append("CANDIDATE_EXPIRED")

        if context.expected_slippage_points < 0:
            reasons.append("INVALID_SLIPPAGE")
        if context.expected_slippage_points > D(p.max_slippage_points):
            reasons.append("SLIPPAGE_LIMIT")
        adverse_slippage = max(D("0"), context.expected_slippage_points) * point
        executable = (ask + adverse_slippage) if candidate.side == "BUY" else (bid - adverse_slippage)
        if candidate.side == "BUY":
            if not (candidate.stop_loss < executable < candidate.take_profit):
                reasons.append("INVALID_PRICE_ORDER")
        elif candidate.side == "SELL":
            if not (candidate.take_profit < executable < candidate.stop_loss):
                reasons.append("INVALID_PRICE_ORDER")

        risk_distance = abs(executable - candidate.stop_loss)
        reward_distance = abs(candidate.take_profit - executable)
        rr = reward_distance / risk_distance if risk_distance > 0 else D("0")
        if risk_distance <= 0:
            reasons.append("ZERO_STOP_DISTANCE")
        elif rr < p.min_risk_reward:
            reasons.append("RR_TOO_LOW")

        if candidate.age_seconds > D(p.max_signal_age_seconds):
            reasons.append("STALE_SIGNAL")
        if context.tick_age_seconds > D(p.max_tick_age_seconds):
            reasons.append("STALE_TICK")
        if not contract.trade_allowed:
            reasons.append("TRADING_NOT_ALLOWED")
        if not contract.market_orders_allowed:
            reasons.append("ORDER_TYPE_NOT_ALLOWED")
        if not contract.session_open:
            reasons.append("SESSION_CLOSED")
        if candidate.reference_entry > 0:
            drift_pct = abs(executable - candidate.reference_entry) / candidate.reference_entry * D("100")
            if drift_pct > p.max_price_drift_pct:
                reasons.append("PRICE_DRIFT_LIMIT")

        spread_points = (ask - bid) / point
        if spread_points > D(p.max_spread_points):
            reasons.append("SPREAD_LIMIT")
        stop_points = risk_distance / point if contract.point else D("0")
        if stop_points < D(contract.trade_stops_level):
            reasons.append("STOP_LEVEL_VIOLATION")

        if context.active_orders >= p.max_active_orders:
            reasons.append("MAX_ACTIVE_ORDERS")
        if context.cooldown_active:
            reasons.append("COOLDOWN_ACTIVE")

        if context.daily_reference_equity is None:
            reasons.append("MISSING_DAILY_REFERENCE_EQUITY")
            daily_reference = D("0")
        else:
            daily_reference = context.daily_reference_equity + context.daily_net_cash_flow
        if context.weekly_reference_equity is None:
            reasons.append("MISSING_WEEKLY_REFERENCE_EQUITY")
            weekly_reference = D("0")
        else:
            weekly_reference = context.weekly_reference_equity + context.weekly_net_cash_flow
        if daily_reference <= 0:
            reasons.append("INVALID_DAILY_REFERENCE_EQUITY")
        if weekly_reference <= 0:
            reasons.append("INVALID_WEEKLY_REFERENCE_EQUITY")

        per_trade_budget = self._bounded_budget(
            self._pct_amount(equity, p.max_risk_per_trade_pct), p.max_risk_per_trade_amount
        )
        total_budget = self._bounded_budget(
            self._pct_amount(equity, p.max_total_open_risk_pct), p.max_total_open_risk_amount
        )
        correlated_budget = self._pct_amount(equity, p.max_correlated_risk_pct)
        daily_budget = self._bounded_budget(
            self._pct_amount(daily_reference, p.daily_loss_limit_pct), p.daily_loss_limit_amount
        )
        weekly_budget = self._bounded_budget(
            self._pct_amount(weekly_reference, p.weekly_loss_limit_pct), p.weekly_loss_limit_amount
        )
        dd_budget = self._bounded_budget(self._pct_amount(equity, p.max_drawdown_pct), p.max_drawdown_amount)

        total_open_risk = context.total_open_risk_amount
        correlated_open_risk = context.correlated_open_risk_amount
        if total_open_risk >= total_budget:
            reasons.append("TOTAL_OPEN_RISK_LIMIT")
        if correlated_open_risk >= correlated_budget:
            reasons.append("CORRELATED_RISK_LIMIT")
        if context.daily_realized_loss_amount >= daily_budget:
            reasons.append("DAILY_LOSS_LIMIT")
        if context.weekly_realized_loss_amount >= weekly_budget:
            reasons.append("WEEKLY_LOSS_LIMIT")
        if context.drawdown_amount >= dd_budget:
            reasons.append("DRAWDOWN_LIMIT")

        min_vol = D(str(contract.volume_min))
        min_loss = abs(calc_profit(candidate.side, candidate.symbol, min_vol, executable, candidate.stop_loss))
        if min_loss <= 0:
            reasons.append("INVALID_PROFIT_CALC")
            raw_volume = D("0")
        else:
            raw_volume = per_trade_budget / (min_loss / min_vol)

        volume = self._floor_volume(raw_volume, contract)
        if volume <= 0:
            reasons.append("MIN_VOLUME_EXCEEDS_RISK")

        projected_loss = D("0")
        margin = D("0")
        if volume > 0:
            projected_loss = abs(calc_profit(candidate.side, candidate.symbol, volume, executable, candidate.stop_loss))
            projected_loss += p.conservative_fee_per_lot * volume
            margin = abs(calc_margin(candidate.side, candidate.symbol, volume, executable))
            if projected_loss > per_trade_budget:
                reasons.append("PER_TRADE_RISK_LIMIT")
            if total_open_risk + projected_loss > total_budget:
                reasons.append("TOTAL_OPEN_RISK_LIMIT")
            if correlated_open_risk + projected_loss > correlated_budget:
                reasons.append("CORRELATED_RISK_LIMIT")
            reserve = equity * p.min_margin_reserve_pct / D("100")
            if D(str(account.margin_free)) - margin < reserve:
                reasons.append("MARGIN_RESERVE_LIMIT")

        unique = tuple(dict.fromkeys(reasons))
        approved = not unique
        return BrokerRiskResult(
            candidate_id=candidate.candidate_id,
            approved=approved,
            reason_codes=unique,
            normalized_symbol=contract.symbol,
            normalized_volume=volume if approved else D("0"),
            executable_entry=executable,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            projected_loss_account_currency=projected_loss if approved else D("0"),
            margin_required=margin if approved else D("0"),
            risk_profile_fingerprint=p.fingerprint,
            safety_snapshot_fingerprint=safety.fingerprint,
            expires_at_utc=candidate.expires_at_utc.astimezone(timezone.utc),
        )
