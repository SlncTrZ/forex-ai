from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from forex_ai.advisory.models import Advisory, AdvisoryAction
from forex_ai.mt5.contracts import AccountSnapshot, SafetySnapshot, SymbolContract, TickSnapshot
from forex_ai.risk.broker_engine import apply_fixed_volume, BrokerAwareRiskEngine, BrokerRiskResult, MarginCalculator, PendingExposure, ProfitCalculator, RiskContext
from forex_ai.risk.profile import RiskProfile
from forex_ai.strategy.v1.contracts import MarketSnapshot, StrategyConfig, StrategyResult
from forex_ai.config import load_fixed_lot
from forex_ai.strategy.config import StrategyConfigSnapshot, bundled_strategy_snapshot
from forex_ai.strategy.v1.breakout_retest import evaluate as evaluate_breakout_retest
from forex_ai.strategy.v1.inside_bar_momentum_breakout import evaluate as evaluate_inside_bar
from forex_ai.strategy.v1.trend_pullback import evaluate as evaluate_pullback
from forex_ai.strategy.v1.volatility_breakout import evaluate as evaluate_breakout

from forex_ai.journal.integration_repository import persist_advisory, persist_candidate, persist_risk_result, persist_safety_snapshot
from .adapters import candidate_input


StrategyCallable = Callable[[MarketSnapshot, StrategyConfig, datetime], StrategyResult]


@dataclass(frozen=True)
class StrategyBinding:
    evaluate: StrategyCallable
    config: StrategyConfig


@dataclass(frozen=True)
class IntegratedDecision:
    strategy_result: StrategyResult
    advisory: Advisory | None
    risk_result: BrokerRiskResult | None
    blocked_reasons: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        return self.risk_result is not None and self.risk_result.approved and not self.blocked_reasons


def production_strategy_bindings(snapshot: StrategyConfigSnapshot) -> tuple[StrategyBinding, ...]:
    bindings: list[StrategyBinding] = []
    # Same-scan tie-break priority for the prospective XAU portfolio. This does
    # not override time priority: an already-open broker position is still
    # authoritative and blocks all later signals through the risk context.
    if snapshot.enabled("inside_bar_momentum_breakout_v1"):
        bindings.append(StrategyBinding(evaluate_inside_bar, snapshot.config_for("inside_bar_momentum_breakout_v1")))
    if snapshot.enabled("breakout_retest_v1"):
        bindings.append(StrategyBinding(evaluate_breakout_retest, snapshot.config_for("breakout_retest_v1")))
    if snapshot.enabled("trend_pullback_v1"):
        bindings.append(StrategyBinding(evaluate_pullback, snapshot.config_for("trend_pullback_v1")))
    if snapshot.enabled("volatility_breakout_v1"):
        bindings.append(StrategyBinding(evaluate_breakout, snapshot.config_for("volatility_breakout_v1")))
    if not bindings:
        raise ValueError("at least one production strategy must be enabled")
    return tuple(bindings)


class DecisionOrchestrator:
    """Joins Strategy V1 -> optional Advisory -> deterministic RiskEngine.

    This component does not send broker orders. Execution remains a separate,
    explicitly armed phase after the decision has been persisted.
    """

    def __init__(
        self,
        *,
        db_path: Path,
        risk_profile: RiskProfile,
        strategies: tuple[StrategyBinding, ...] | None = None,
    ):
        self.db_path = db_path
        self.risk_profile = risk_profile
        self.strategies = strategies or production_strategy_bindings(bundled_strategy_snapshot())

    def scan(
        self,
        market: MarketSnapshot,
        *,
        account: AccountSnapshot,
        contract: SymbolContract,
        tick: TickSnapshot,
        safety: SafetySnapshot,
        risk_context: RiskContext,
        calc_profit: ProfitCalculator,
        calc_margin: MarginCalculator,
        now_utc: datetime,
        advisory_for: Callable[[str], Advisory | None] | None = None,
        deterministic_gate_ok: bool = True,
        deterministic_gate_reason: str = "DETERMINISTIC_GATE_BLOCKED",
    ) -> tuple[IntegratedDecision, ...]:
        now = now_utc.astimezone(timezone.utc)
        persist_safety_snapshot(self.db_path, safety)
        decisions: list[IntegratedDecision] = []
        rolling_context = risk_context
        claimed_in_scan = False

        for binding in self.strategies:
            strategy_result = binding.evaluate(market, binding.config, now)
            candidate = strategy_result.candidate
            if candidate is None:
                decisions.append(IntegratedDecision(strategy_result, None, None, strategy_result.no_setup_reason_codes))
                continue

            persist_candidate(self.db_path, candidate)
            if not deterministic_gate_ok:
                decisions.append(IntegratedDecision(strategy_result, None, None, (deterministic_gate_reason,)))
                continue

            advisory = advisory_for(candidate.candidate_id) if advisory_for else None
            if advisory is not None:
                if advisory.candidate_id != candidate.candidate_id:
                    raise ValueError("advisory candidate_id mismatch")
                persist_advisory(self.db_path, advisory, created_at_utc=now)
                if advisory.expires_at_utc.astimezone(timezone.utc) <= now:
                    decisions.append(IntegratedDecision(strategy_result, advisory, None, ("ADVISORY_EXPIRED",)))
                    continue
                if advisory.action is AdvisoryAction.VETO:
                    decisions.append(IntegratedDecision(strategy_result, advisory, None, ("ADVISORY_VETO",)))
                    continue

            multiplier = Decimal("1")
            if advisory is not None and advisory.action is AdvisoryAction.REDUCE_RISK:
                multiplier = Decimal(str(advisory.risk_multiplier))
            effective_profile = self._effective_profile(multiplier)
            risk_result = BrokerAwareRiskEngine(effective_profile).evaluate(
                candidate_input(candidate, now_utc=now),
                account=account,
                contract=contract,
                tick=tick,
                safety=safety,
                context=rolling_context,
                calc_profit=calc_profit,
                calc_margin=calc_margin,
                now_utc=now,
            )
            fixed_lot_raw = load_fixed_lot()
            risk_result = apply_fixed_volume(risk_result, fixed_volume=Decimal(fixed_lot_raw) if fixed_lot_raw is not None else None, calc_profit=calc_profit, calc_margin=calc_margin)
            if claimed_in_scan and not risk_result.approved:
                risk_result = replace(
                    risk_result,
                    reason_codes=tuple(dict.fromkeys((*risk_result.reason_codes, "PORTFOLIO_SLOT_CLAIMED"))),
                )
            persist_risk_result(self.db_path, risk_result, created_at_utc=now)
            decisions.append(IntegratedDecision(strategy_result, advisory, risk_result, risk_result.reason_codes))
            if risk_result.approved:
                rolling_context = replace(
                    rolling_context,
                    pending_exposures=(
                        *rolling_context.pending_exposures,
                        PendingExposure(
                            intent_id=f"scan-approved:{candidate.candidate_id}",
                            symbol=risk_result.normalized_symbol,
                            side=risk_result.side,
                            volume=risk_result.normalized_volume,
                            entry=risk_result.executable_entry,
                            stop_loss=risk_result.stop_loss,
                            take_profit=risk_result.take_profit,
                            correlation_group=rolling_context.proposed_correlation_group,
                        ),
                    ),
                )
                claimed_in_scan = True

        return tuple(decisions)

    def _effective_profile(self, multiplier: Decimal) -> RiskProfile:
        if multiplier < 0 or multiplier > 1:
            raise ValueError("advisory multiplier must be within 0..1")
        if multiplier == 1:
            return self.risk_profile
        if multiplier == 0:
            raise ValueError("zero multiplier must be represented as VETO")
        amount = self.risk_profile.max_risk_per_trade_amount
        return self.risk_profile.model_copy(update={
            "max_risk_per_trade_pct": self.risk_profile.max_risk_per_trade_pct * multiplier,
            "max_risk_per_trade_amount": amount * multiplier if amount is not None else None,
        })
