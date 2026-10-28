"""Multi-Horizon Portfolio Allocator & Risk Firewall — Dual horizon budgeting, conflict resolution, circuit breakers.
Wing: code | Topic: portfolio_risk | Updated: 2026-10-28 12:22
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

UTC = timezone.utc


class TradingHorizon(str, Enum):
    SCALPING = "SCALPING"          # M1/M5
    DAY_TRADING = "DAY_TRADING"    # M15/H1


@dataclass(frozen=True)
class PortfolioBudgetConfig:
    max_daily_portfolio_risk_pct: float = 3.0
    day_trading_share: float = 0.80  # 80% ngân sách cho Day Trading (2.4%)
    scalping_share: float = 0.20     # 20% ngân sách cho Scalping (0.6%)
    max_open_trades_scalp: int = 2
    max_open_trades_day: int = 2


@dataclass
class HorizonState:
    realized_loss_today: float = 0.0
    unrealized_loss_today: float = 0.0
    active_positions_count: int = 0
    circuit_broken: bool = False
    lock_reason: Optional[str] = None


@dataclass(frozen=True)
class AllocationVerdict:
    approved: bool
    horizon: TradingHorizon
    reason: str
    allowed_risk_pct: float


class PortfolioRiskController:
    """Kiểm soát rủi ro đa chân trời (Multi-Horizon Risk Firewall)."""

    def __init__(self, config: Optional[PortfolioBudgetConfig] = None):
        self.config = config or PortfolioBudgetConfig()
        self.states: Dict[TradingHorizon, HorizonState] = {
            TradingHorizon.SCALPING: HorizonState(),
            TradingHorizon.DAY_TRADING: HorizonState(),
        }
        self.current_day_str: Optional[str] = None

    def _rollover_day_if_needed(self, now_utc: datetime) -> None:
        day_str = now_utc.astimezone(UTC).strftime("%Y-%m-%d")
        if self.current_day_str != day_str:
            self.current_day_str = day_str
            for state in self.states.values():
                state.realized_loss_today = 0.0
                state.unrealized_loss_today = 0.0
                state.circuit_broken = False
                state.lock_reason = None

    def update_loss(
        self,
        horizon: TradingHorizon,
        realized_delta: float,
        unrealized_current: float,
        equity: float,
        now_utc: Optional[datetime] = None,
    ) -> None:
        if now_utc is not None:
            self._rollover_day_if_needed(now_utc)
        state = self.states[horizon]
        state.realized_loss_today += realized_delta
        state.unrealized_loss_today = unrealized_current

        # Kiểm tra Circuit Breaker cho riêng nhánh đó
        max_loss_pct = (
            self.config.max_daily_portfolio_risk_pct * self.config.scalping_share
            if horizon == TradingHorizon.SCALPING
            else self.config.max_daily_portfolio_risk_pct * self.config.day_trading_share
        )
        total_loss = state.realized_loss_today + max(0.0, state.unrealized_loss_today)
        loss_pct = (total_loss / equity * 100.0) if equity > 0 else 0.0

        if loss_pct >= max_loss_pct:
            state.circuit_broken = True
            state.lock_reason = f"CIRCUIT_BREAKER_TRIPPED: Loss {loss_pct:.2f}% >= Max {max_loss_pct:.2f}%"

    def check_order_admission(
        self,
        horizon: TradingHorizon,
        symbol: str,
        side: str,  # "BUY" | "SELL"
        equity: float,
        now_utc: datetime,
        open_positions: List[dict],  # list of {'symbol': str, 'side': str, 'horizon': TradingHorizon}
    ) -> AllocationVerdict:
        self._rollover_day_if_needed(now_utc)
        state = self.states[horizon]

        # 1. Kiểm tra Circuit Breaker nội bộ
        if state.circuit_broken:
            return AllocationVerdict(False, horizon, state.lock_reason or "CIRCUIT_BREAKER", 0.0)

        # 2. Kiểm tra giới hạn số lệnh đồng thời
        max_trades = (
            self.config.max_open_trades_scalp
            if horizon == TradingHorizon.SCALPING
            else self.config.max_open_trades_day
        )
        active_in_horizon = sum(1 for p in open_positions if p.get("horizon") == horizon)
        if active_in_horizon >= max_trades:
            return AllocationVerdict(False, horizon, f"MAX_OPEN_TRADES_REACHED:{active_in_horizon}/{max_trades}", 0.0)

        # 3. Tường lửa Xung đột ngược chiều (Cross-Horizon Conflict Firewall)
        # Nếu Day Trading đang có lệnh ngược chiều cùng symbol -> CẤM Scalping đánh ngược
        if horizon == TradingHorizon.SCALPING:
            for p in open_positions:
                if p.get("horizon") == TradingHorizon.DAY_TRADING and p.get("symbol") == symbol:
                    if p.get("side") != side:
                        return AllocationVerdict(
                            False,
                            horizon,
                            f"CROSS_HORIZON_CONFLICT: Day Trading holds {p.get('side')} on {symbol}",
                            0.0,
                        )

        # 4. Tính toán rủi ro khả dụng cho lệnh
        allowed_risk_pct = (
            self.config.max_daily_portfolio_risk_pct * self.config.scalping_share / self.config.max_open_trades_scalp
            if horizon == TradingHorizon.SCALPING
            else self.config.max_daily_portfolio_risk_pct * self.config.day_trading_share / self.config.max_open_trades_day
        )

        return AllocationVerdict(True, horizon, "ADMITTED", round(allowed_risk_pct, 4))
