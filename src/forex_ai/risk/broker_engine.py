from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Callable

from forex_ai.mt5.contracts import AccountSnapshot, BrokerPosition, SafetySnapshot, SymbolContract, TickSnapshot
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
class ExistingPosition:
    intent_id: str
    position: BrokerPosition
    correlation_group: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id is required")


@dataclass(frozen=True)
class PendingExposure:
    intent_id: str
    symbol: str
    side: str
    volume: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    correlation_group: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_id:
            raise ValueError("intent_id is required")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("pending exposure side must be BUY or SELL")
        values = (self.volume, self.entry, self.stop_loss, self.take_profit)
        if any(not value.is_finite() for value in values):
            raise ValueError("pending exposure numeric values must be finite")
        if self.volume <= 0:
            raise ValueError("pending exposure volume must be > 0")
        if self.entry <= 0 or self.stop_loss <= 0 or self.take_profit <= 0:
            raise ValueError("pending exposure prices must be > 0")
        if self.side == "BUY" and not (self.stop_loss < self.entry < self.take_profit):
            raise ValueError("BUY pending exposure requires SL < entry < TP")
        if self.side == "SELL" and not (self.take_profit < self.entry < self.stop_loss):
            raise ValueError("SELL pending exposure requires TP < entry < SL")


@dataclass(frozen=True)
class RiskContext:
    active_intent_ids: tuple[str, ...] = ()
    existing_positions: tuple[ExistingPosition, ...] = ()
    pending_exposures: tuple[PendingExposure, ...] = ()
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

    def __post_init__(self) -> None:
        values = (
            self.tick_age_seconds, self.expected_slippage_points,
            self.daily_realized_loss_amount, self.weekly_realized_loss_amount,
            self.drawdown_amount, self.daily_net_cash_flow, self.weekly_net_cash_flow,
        )
        optional = (self.daily_reference_equity, self.weekly_reference_equity)
        if any(not value.is_finite() for value in values):
            raise ValueError("risk context numeric values must be finite")
        if any(value is not None and not value.is_finite() for value in optional):
            raise ValueError("risk context reference equity must be finite")
        if self.tick_age_seconds < 0:
            raise ValueError("tick_age_seconds must be >= 0")
        if self.daily_realized_loss_amount < 0 or self.weekly_realized_loss_amount < 0 or self.drawdown_amount < 0:
            raise ValueError("loss and drawdown amounts must be >= 0")

    @property
    def active_orders(self) -> int:
        ids = set(self.active_intent_ids)
        ids.update(item.intent_id for item in self.existing_positions)
        ids.update(item.intent_id for item in self.pending_exposures)
        return len(ids)


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
    def _calculated_decimal(call: Callable[[], Decimal]) -> Decimal | None:
        try:
            value = call()
            if value is None:
                return None
            result = D(str(value))
            return result if result.is_finite() else None
        except Exception:
            return None

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
        price_step = D(str(contract.trade_tick_size or contract.point))

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
        candidate_numbers = (
            candidate.reference_entry, candidate.stop_loss, candidate.take_profit, candidate.age_seconds,
        )
        if any(not value.is_finite() for value in candidate_numbers):
            reasons.append("INVALID_NUMERIC_VALUE")
            unique = tuple(dict.fromkeys(reasons))
            return BrokerRiskResult(
                candidate_id=candidate.candidate_id, approved=False, reason_codes=unique,
                normalized_symbol=contract.symbol, normalized_volume=D("0"),
                executable_entry=ask if candidate.side == "BUY" else bid,
                stop_loss=candidate.stop_loss, take_profit=candidate.take_profit,
                projected_loss_account_currency=D("0"), margin_required=D("0"),
                risk_profile_fingerprint=p.fingerprint, safety_snapshot_fingerprint=safety.fingerprint,
                expires_at_utc=candidate.expires_at_utc.astimezone(timezone.utc),
            )

        if context.expected_slippage_points < 0:
            reasons.append("INVALID_SLIPPAGE")
        if context.expected_slippage_points > D(p.max_slippage_points):
            reasons.append("SLIPPAGE_LIMIT")
        adverse_slippage = max(D("0"), context.expected_slippage_points) * point
        executable = (ask + adverse_slippage) if candidate.side == "BUY" else (bid - adverse_slippage)
        if any(
            (value / price_step) != (value / price_step).to_integral_value()
            for value in (executable, candidate.stop_loss, candidate.take_profit)
        ):
            reasons.append("PRICE_TICK_ALIGNMENT")
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
        target_points = reward_distance / point if contract.point else D("0")
        if stop_points < D(contract.trade_stops_level):
            reasons.append("STOP_LEVEL_VIOLATION")
        if target_points < D(contract.trade_stops_level):
            reasons.append("TARGET_LEVEL_VIOLATION")
        if D(contract.trade_freeze_level) > 0 and min(stop_points, target_points) < D(contract.trade_freeze_level):
            reasons.append("PROTECTION_FREEZE_LEVEL_VIOLATION")

        quantified_intent_ids = {item.intent_id for item in context.existing_positions}
        quantified_intent_ids.update(item.intent_id for item in context.pending_exposures)
        if set(context.active_intent_ids) - quantified_intent_ids:
            reasons.append("UNQUANTIFIED_ACTIVE_EXPOSURE")
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

        total_open_risk = D("0")
        correlated_open_risk = D("0")
        for existing in context.existing_positions:
            position = existing.position
            if position.sl <= 0:
                reasons.append("UNPROTECTED_EXISTING_POSITION")
                continue
            remaining_pnl = self._calculated_decimal(lambda: calc_profit(
                position.side,
                position.symbol,
                D(str(position.volume)),
                D(str(position.price_current)),
                D(str(position.sl)),
            ))
            if remaining_pnl is None:
                reasons.append("INVALID_EXISTING_RISK_CALC")
                continue
            remaining_loss = max(D("0"), -remaining_pnl)
            remaining_loss += p.conservative_fee_per_lot * D(str(position.volume))
            total_open_risk += remaining_loss
            if context.proposed_correlation_group is not None and existing.correlation_group == context.proposed_correlation_group:
                correlated_open_risk += remaining_loss
        for pending in context.pending_exposures:
            pending_pnl = self._calculated_decimal(lambda pending=pending: calc_profit(
                pending.side, pending.symbol, pending.volume, pending.entry, pending.stop_loss,
            ))
            if pending_pnl is None:
                reasons.append("INVALID_PENDING_RISK_CALC")
                continue
            pending_loss = max(D("0"), -pending_pnl)
            pending_loss += p.conservative_fee_per_lot * pending.volume
            total_open_risk += pending_loss
            if context.proposed_correlation_group is not None and pending.correlation_group == context.proposed_correlation_group:
                correlated_open_risk += pending_loss
        if total_open_risk >= total_budget:
            reasons.append("TOTAL_OPEN_RISK_LIMIT")
        if context.proposed_correlation_group is not None and correlated_open_risk >= correlated_budget:
            reasons.append("CORRELATED_RISK_LIMIT")
        if context.daily_realized_loss_amount >= daily_budget:
            reasons.append("DAILY_LOSS_LIMIT")
        if context.weekly_realized_loss_amount >= weekly_budget:
            reasons.append("WEEKLY_LOSS_LIMIT")
        if context.drawdown_amount >= dd_budget:
            reasons.append("DRAWDOWN_LIMIT")

        min_vol = D(str(contract.volume_min))
        min_pnl = self._calculated_decimal(
            lambda: calc_profit(candidate.side, candidate.symbol, min_vol, executable, candidate.stop_loss)
        )
        if min_pnl is None or min_pnl == 0:
            reasons.append("INVALID_PROFIT_CALC")
            min_loss = D("0")
            raw_volume = D("0")
        else:
            min_loss = abs(min_pnl)
            risk_per_lot = (min_loss / min_vol) + p.conservative_fee_per_lot
            raw_volume = per_trade_budget / risk_per_lot if risk_per_lot > 0 else D("0")

        upper_volume = self._floor_volume(raw_volume, contract)
        volume = D("0")
        projected_loss = D("0")
        margin = D("0")
        if upper_volume <= 0:
            reasons.append("MIN_VOLUME_EXCEEDS_RISK")
        else:
            minimum = D(str(contract.volume_min))
            step = D(str(contract.volume_step))
            max_index = int(((upper_volume - minimum) / step).to_integral_value(rounding=ROUND_FLOOR))
            low = 0
            high = max_index
            calc_failed = False
            while low <= high:
                mid = (low + high) // 2
                trial_volume = minimum + step * D(mid)
                trial_pnl = self._calculated_decimal(
                    lambda trial_volume=trial_volume: calc_profit(
                        candidate.side, candidate.symbol, trial_volume, executable, candidate.stop_loss
                    )
                )
                if trial_pnl is None:
                    reasons.append("INVALID_PROFIT_CALC")
                    calc_failed = True
                    break
                trial_loss = abs(trial_pnl) + p.conservative_fee_per_lot * trial_volume
                if trial_loss <= per_trade_budget:
                    volume = trial_volume
                    projected_loss = trial_loss
                    low = mid + 1
                else:
                    high = mid - 1
            if not calc_failed and volume <= 0:
                reasons.append("MIN_VOLUME_EXCEEDS_RISK")
            if volume > 0:
                calculated_margin = self._calculated_decimal(
                    lambda: calc_margin(candidate.side, candidate.symbol, volume, executable)
                )
                if calculated_margin is None or calculated_margin < 0:
                    reasons.append("INVALID_MARGIN_CALC")
                else:
                    margin = calculated_margin
            if projected_loss > per_trade_budget:
                reasons.append("PER_TRADE_RISK_LIMIT")
            if total_open_risk + projected_loss > total_budget:
                reasons.append("TOTAL_OPEN_RISK_LIMIT")
            if (
                context.proposed_correlation_group is not None
                and correlated_open_risk + projected_loss > correlated_budget
            ):
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
