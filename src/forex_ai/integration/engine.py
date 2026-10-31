from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from forex_ai.advisory.models import Advisory, AdvisoryAction
from forex_ai.advisory.regime_router import RegimeDecision, route_candidate
from forex_ai.advisory.confluence_scoring import ConfluenceDecision
from forex_ai.mt5.contracts import AccountSnapshot, SafetySnapshot, SymbolContract, TickSnapshot
from forex_ai.risk.broker_engine import apply_fixed_volume, BrokerAwareRiskEngine, BrokerRiskResult, MarginCalculator, PendingExposure, ProfitCalculator, RiskContext
from forex_ai.risk.profile import RiskProfile
from forex_ai.strategy.v1.contracts import MarketSnapshot, StrategyConfig, StrategyResult
from forex_ai.config import load_fixed_lot
from forex_ai.strategy.config import StrategyConfigSnapshot, bundled_strategy_snapshot
from forex_ai.strategy.v2.btc_squeeze_breakout_m15 import evaluate as evaluate_btc_squeeze
from forex_ai.strategy.v2.ichimoku_mtf import evaluate as evaluate_ichimoku
from forex_ai.strategy.v2.keylevel_breakout import evaluate as evaluate_keylevel
from forex_ai.strategy.v2.mtf_trend_continuation import evaluate as evaluate_mtf_trend
from forex_ai.strategy.v2.pro_volatility_expansion_v2 import evaluate as evaluate_pro_volatility
from forex_ai.strategy.v2.range_liquidity_trap_m5 import evaluate as evaluate_range_liquidity_trap
from forex_ai.strategy.v2.smc_pro_fvg import evaluate as evaluate_smc
from forex_ai.strategy.v2.supertrend_stochastic_m5 import evaluate as evaluate_supertrend_stoch
from forex_ai.strategy.v2.supertrend_volume_m5 import evaluate as evaluate_supertrend

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
    confluence: ConfluenceDecision | None = None

    @property
    def executable(self) -> bool:
        return self.risk_result is not None and self.risk_result.approved and not self.blocked_reasons


def production_strategy_bindings(snapshot: StrategyConfigSnapshot) -> tuple[StrategyBinding, ...]:
    bindings: list[StrategyBinding] = []
    # Same-scan tie-break priority for the prospective portfolio. This does
    # not override time priority: an already-open broker position is still
    # authoritative and blocks all later signals through the risk context.
    if snapshot.enabled("supertrend_volume_m5"):
        bindings.append(StrategyBinding(evaluate_supertrend, snapshot.config_for("supertrend_volume_m5")))
    if snapshot.enabled("supertrend_stochastic_m5"):
        bindings.append(StrategyBinding(evaluate_supertrend_stoch, snapshot.config_for("supertrend_stochastic_m5")))
    if snapshot.enabled("keylevel_breakout_v2"):
        bindings.append(StrategyBinding(evaluate_keylevel, snapshot.config_for("keylevel_breakout_v2")))
    if snapshot.enabled("smc_pro_fvg_v2"):
        bindings.append(StrategyBinding(evaluate_smc, snapshot.config_for("smc_pro_fvg_v2")))
    if snapshot.enabled("btc_squeeze_breakout_m15"):
        bindings.append(StrategyBinding(evaluate_btc_squeeze, snapshot.config_for("btc_squeeze_breakout_m15")))
    if snapshot.enabled("mtf_trend_continuation_v2"):
        bindings.append(StrategyBinding(evaluate_mtf_trend, snapshot.config_for("mtf_trend_continuation_v2")))
    if snapshot.enabled("pro_volatility_expansion_v2"):
        bindings.append(StrategyBinding(evaluate_pro_volatility, snapshot.config_for("pro_volatility_expansion_v2")))
    if snapshot.enabled("ichimoku_mtf_v2"):
        bindings.append(StrategyBinding(evaluate_ichimoku, snapshot.config_for("ichimoku_mtf_v2")))
    if snapshot.enabled("range_liquidity_trap_m5"):
        bindings.append(StrategyBinding(evaluate_range_liquidity_trap, snapshot.config_for("range_liquidity_trap_m5")))
    if not bindings:
        raise ValueError("at least one production strategy must be enabled")
    return tuple(bindings)


def strategy_eligible_for_symbol(strategy_id: str, symbol: str) -> bool:
    """Safe default: BTC orders only from BTC strategy; never cross-run FX logic."""
    name = symbol.upper()
    # Exact allowlist matches the existing risk.yaml broker symbol aliases.
    if name in {"BTCUSD", "BTCUSDM", "BTCUSDC"}:
        return strategy_id == "btc_squeeze_breakout_m15"
    if name in {
        "XAUUSD", "XAUUSDM", "XAUUSDC",
        "EURUSD", "EURUSDM", "EURUSDC",
        "USDJPY", "USDJPYM", "USDJPYC",
    }:
        return strategy_id != "btc_squeeze_breakout_m15"
    return False


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
        regime_decision: RegimeDecision | None = None,
        confluence_for: Callable[[StrategyResult, RegimeDecision], ConfluenceDecision] | None = None,
    ) -> tuple[IntegratedDecision, ...]:
        now = now_utc.astimezone(timezone.utc)
        persist_safety_snapshot(self.db_path, safety)
        decisions: list[IntegratedDecision] = []
        rolling_context = risk_context
        claimed_in_scan = False

        for binding in self.strategies:
            if not strategy_eligible_for_symbol(binding.config.version.strategy_id, market.symbol):
                continue
            strategy_result = binding.evaluate(market, binding.config, now)
            candidate = strategy_result.candidate
            if candidate is None:
                decisions.append(IntegratedDecision(strategy_result, None, None, strategy_result.no_setup_reason_codes))
                continue

            persist_candidate(self.db_path, candidate)
            if not deterministic_gate_ok:
                decisions.append(IntegratedDecision(strategy_result, None, None, (deterministic_gate_reason,)))
                continue

            if regime_decision is not None:
                veto = route_candidate(
                    regime_decision, strategy_id=binding.config.version.strategy_id,
                    side=candidate.side,
                    evidence_codes=strategy_result.evidence.reason_codes,
                )
                if veto:
                    decisions.append(IntegratedDecision(strategy_result, None, None, (veto,)))
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

            confluence = None
            if confluence_for is not None:
                if regime_decision is None:
                    raise ValueError("confluence requires regime")
                confluence = confluence_for(strategy_result, regime_decision)
                if confluence.candidate_id != candidate.candidate_id:
                    raise ValueError("confluence candidate mismatch")
                if not confluence.allowed:
                    decisions.append(IntegratedDecision(
                        strategy_result, advisory, None,
                        (confluence.reason_code,), confluence,
                    ))
                    continue

            multiplier = Decimal("1")
            if advisory is not None and advisory.action is AdvisoryAction.REDUCE_RISK:
                multiplier = Decimal(str(advisory.risk_multiplier))
            if confluence is not None:
                multiplier = min(multiplier, confluence.multiplier)
            effective_profile = self._effective_profile(multiplier)
            risk_result = BrokerAwareRiskEngine(effective_profile).evaluate(
                candidate_input(candidate, now_utc=now, contract=contract),
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
            decisions.append(IntegratedDecision(strategy_result, advisory, risk_result, risk_result.reason_codes, confluence))
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
