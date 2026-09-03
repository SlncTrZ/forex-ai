from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TradeProposal:
    symbol: str
    side: str
    volume: float
    entry: float
    stop_loss: float
    take_profit: float
    rr: float
    age_seconds: float = 0.0


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    approved_volume: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)


class RiskEngine:
    """Deterministic final gate. LLM output can never bypass this layer."""

    def __init__(self, risk_config: dict[str, Any], mode: str):
        self.cfg = risk_config
        self.mode = mode

    def evaluate(
        self,
        proposal: TradeProposal,
        *,
        symbol_info: dict[str, Any] | None,
        account: dict[str, Any] | None,
        current_positions: int,
        daily_realized_loss_pct: float = 0.0,
        daily_equity_drawdown_pct: float = 0.0,
    ) -> RiskDecision:
        reasons: list[str] = []
        limits = self.cfg.get("limits", {})
        allowed = set(self.cfg.get("allowed_symbols", []))

        if not self.cfg.get("enabled", True):
            reasons.append("RISK_ENGINE_DISABLED")
        if not self.cfg.get("execution_enabled", False):
            reasons.append("EXECUTION_DISABLED")
        if self.mode not in {"CENT_GUARDED", "CENT_EXPERIMENT"}:
            reasons.append("MODE_NOT_LIVE")
        if proposal.symbol not in allowed:
            reasons.append("SYMBOL_NOT_ALLOWED")
        if proposal.side not in {"BUY", "SELL"}:
            reasons.append("INVALID_SIDE")
        if account is None:
            reasons.append("NO_ACCOUNT")
        if symbol_info is None:
            reasons.append("NO_SYMBOL_INFO")
        if proposal.rr < float(limits.get("min_risk_reward", 1.5)):
            reasons.append("RR_TOO_LOW")
        if proposal.age_seconds > float(limits.get("max_signal_age_seconds", 30)):
            reasons.append("STALE_SIGNAL")
        if current_positions >= int(limits.get("max_simultaneous_positions", 2)):
            reasons.append("POSITION_LIMIT")
        if daily_realized_loss_pct >= float(limits.get("max_daily_realized_loss_pct", 1.0)):
            reasons.append("DAILY_LOSS_LIMIT")
        if daily_equity_drawdown_pct >= float(limits.get("max_daily_equity_drawdown_pct", 1.0)):
            reasons.append("EQUITY_DD_LIMIT")

        approved_volume = 0.0
        if symbol_info is not None and proposal.volume > 0:
            minimum = float(symbol_info.get("volume_min") or 0)
            maximum = float(symbol_info.get("volume_max") or proposal.volume)
            step = float(symbol_info.get("volume_step") or minimum or 0.01)
            if minimum <= 0:
                reasons.append("INVALID_VOLUME_RULES")
            else:
                requested = min(proposal.volume, maximum)
                steps = int((requested - minimum) / step) if requested >= minimum else -1
                approved_volume = minimum + max(0, steps) * step if steps >= 0 else 0.0
                if approved_volume < minimum:
                    reasons.append("VOLUME_BELOW_MIN")

        return RiskDecision(
            approved=not reasons,
            approved_volume=approved_volume if not reasons else 0.0,
            reasons=tuple(reasons),
        )
