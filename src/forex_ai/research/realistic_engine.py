"""Realistic Event-Driven Backtesting Engine — Micro-path intra-bar stepping and realistic friction.
Wing: code | Topic: realistic_engine | Updated: 2026-10-28 12:05
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Iterable, List, Optional, Sequence

from forex_ai.research.models.dynamic_spread import DynamicSpreadModel
from forex_ai.research.models.simulated_account import SimulatedBrokerAccount
from forex_ai.research.models.slippage_impact import SlippageModel
from forex_ai.risk.tiered_compound import TieredCompoundRiskEngine
from forex_ai.strategy.v1.contracts import CandidateEnvelope, Candle, MarketSnapshot, StrategyConfig, StrategyResult

UTC = timezone.utc
StrategyCallable = Callable[[MarketSnapshot, StrategyConfig, datetime], StrategyResult]


@dataclass(frozen=True)
class RealisticTradeRecord:
    candidate_id: str
    symbol: str
    strategy_id: str
    side: str
    entry_time_utc: datetime
    exit_time_utc: datetime
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    volume: float
    net_pnl: float
    gross_pnl: float
    spread_cost: float
    slippage_cost: float
    commission_cost: float
    swap_cost: float
    exit_reason: str


@dataclass(frozen=True)
class RealisticBacktestReport:
    initial_balance: float
    final_balance: float
    total_net_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_amount: float
    max_drawdown_pct: float
    total_spread_cost: float
    total_slippage_cost: float
    total_commission_cost: float
    total_swap_cost: float
    trades: tuple[RealisticTradeRecord, ...]


class RealisticEventEngine:
    """Engine mô phỏng khớp lệnh thực tế có giải quyết Intra-bar Path Ambiguity."""

    def __init__(
        self,
        strategy: StrategyCallable,
        config: StrategyConfig,
        initial_balance: float = 10_000.0,
        leverage: float = 100.0,
        spread_model: Optional[DynamicSpreadModel] = None,
        slippage_model: Optional[SlippageModel] = None,
        use_tiered_sizing: bool = False,
    ):
        self.strategy = strategy
        self.config = config
        self.initial_balance: float = initial_balance
        self.use_tiered_sizing: bool = use_tiered_sizing
        self.account = SimulatedBrokerAccount(initial_balance=initial_balance, leverage=leverage)
        self.spread_model = spread_model or DynamicSpreadModel()
        self.slippage_model = slippage_model or SlippageModel()
        self.closed_trades: List[RealisticTradeRecord] = []
        self.peak_balance: float = initial_balance
        self.max_drawdown: float = 0.0
        self.max_drawdown_pct: float = 0.0

    def _generate_synthetic_path(self, bar: Candle) -> list[float]:
        """Tái hiện chuỗi giá vi mô trong nến:
        - Bullish: Open -> Low -> High -> Close
        - Bearish: Open -> High -> Low -> Close
        """
        if bar.close >= bar.open:
            return [bar.open, bar.low, bar.high, bar.close]
        return [bar.open, bar.high, bar.low, bar.close]

    def process_snapshot(
        self,
        snapshot: MarketSnapshot,
        clock_utc: datetime,
        sub_bars: Optional[Sequence[Candle]] = None,
        is_news_window: bool = False,
    ) -> None:
        symbol = snapshot.symbol
        time_utc = clock_utc.astimezone(UTC)

        # 1. Cập nhật Swap qua đêm
        self.account.process_daily_rollover(time_utc, {symbol: {"long": -5.0, "short": 1.0}})

        # 2. Tính toán spread hiện tại
        current_spread = self.spread_model.calculate_spread(symbol, time_utc, is_news_window=is_news_window)
        half_spread = current_spread / 2.0
        mid_price = (snapshot.bid + snapshot.ask) / 2.0
        effective_bid = mid_price - half_spread
        effective_ask = mid_price + half_spread
        price_dict = {symbol: {"bid": effective_bid, "ask": effective_ask}}

        # 3. Kiểm tra Stop Out cưỡng chế
        stop_outs = self.account.check_stop_out(price_dict)
        for pid in stop_outs:
            pos = self.account.positions.get(pid)
            if pos:
                exit_p = effective_bid if pos.side == "BUY" else effective_ask
                net_pnl = self.account.close_position(pid, exit_p, time_utc)
                self.closed_trades.append(
                    RealisticTradeRecord(
                        candidate_id=pid,
                        symbol=pos.symbol,
                        strategy_id="SYSTEM",
                        side=pos.side,
                        entry_time_utc=pos.open_time_utc,
                        exit_time_utc=time_utc,
                        entry_price=pos.open_price,
                        exit_price=exit_p,
                        stop_loss=pos.stop_loss,
                        take_profit=pos.take_profit,
                        volume=pos.volume,
                        net_pnl=net_pnl,
                        gross_pnl=net_pnl,
                        spread_cost=current_spread * pos.volume * pos.contract_size,
                        slippage_cost=0.0,
                        commission_cost=self.account.commission_rate * pos.volume * 2.0,
                        swap_cost=pos.accumulated_swap,
                        exit_reason="STOP_OUT",
                    )
                )

        # 4. Kiểm tra các lệnh đang mở trên thanh nến hiện tại
        primary_tf = snapshot.timeframes.get("M5") or snapshot.timeframes.get("M15")
        latest_bar = primary_tf.closed_bars[-1] if primary_tf and primary_tf.closed_bars else None

        if latest_bar and self.account.positions:
            path_points = []
            if sub_bars:
                for sb in sub_bars:
                    path_points.extend(self._generate_synthetic_path(sb))
            else:
                path_points = self._generate_synthetic_path(latest_bar)

            for pid, pos in list(self.account.positions.items()):
                if pos.symbol != symbol:
                    continue

                exit_triggered = False
                exit_price = 0.0
                reason = ""

                # Duyệt tuần tự qua vi cấu trúc lộ trình giá
                for price_point in path_points:
                    if pos.side == "BUY":
                        bid_point = price_point - half_spread
                        if bid_point <= pos.stop_loss:
                            exit_triggered = True
                            # Trượt giá âm cho Stop Loss
                            slip = self.slippage_model.calculate_slippage(symbol, "STOP", pos.volume)
                            exit_price = pos.stop_loss - slip
                            reason = "STOP_LOSS"
                            break
                        elif bid_point >= pos.take_profit:
                            exit_triggered = True
                            # Take profit limit order
                            slip = self.slippage_model.calculate_slippage(symbol, "LIMIT", pos.volume)
                            exit_price = pos.take_profit + slip
                            reason = "TAKE_PROFIT"
                            break
                    else:
                        ask_point = price_point + half_spread
                        if ask_point >= pos.stop_loss:
                            exit_triggered = True
                            slip = self.slippage_model.calculate_slippage(symbol, "STOP", pos.volume)
                            exit_price = pos.stop_loss + slip
                            reason = "STOP_LOSS"
                            break
                        elif ask_point <= pos.take_profit:
                            exit_triggered = True
                            slip = self.slippage_model.calculate_slippage(symbol, "LIMIT", pos.volume)
                            exit_price = pos.take_profit - slip
                            reason = "TAKE_PROFIT"
                            break

                if exit_triggered:
                    contract_size = pos.contract_size
                    if "JPY" in pos.symbol.upper():
                        gross = ((exit_price - pos.open_price if pos.side == "BUY" else pos.open_price - exit_price) * pos.volume * contract_size) / exit_price
                        spread_c = (current_spread * pos.volume * contract_size) / exit_price
                        slip_c = (abs(exit_price - (pos.stop_loss if reason == "STOP_LOSS" else pos.take_profit)) * pos.volume * contract_size) / exit_price
                    else:
                        gross = (exit_price - pos.open_price if pos.side == "BUY" else pos.open_price - exit_price) * pos.volume * contract_size
                        spread_c = current_spread * pos.volume * contract_size
                        slip_c = abs(exit_price - (pos.stop_loss if reason == "STOP_LOSS" else pos.take_profit)) * pos.volume * contract_size

                    comm = self.account.commission_rate * pos.volume * 2.0
                    net_pnl = self.account.close_position(pid, exit_price, time_utc)
                    self.closed_trades.append(
                        RealisticTradeRecord(
                            candidate_id=pid,
                            symbol=pos.symbol,
                            strategy_id="V1",
                            side=pos.side,
                            entry_time_utc=pos.open_time_utc,
                            exit_time_utc=time_utc,
                            entry_price=pos.open_price,
                            exit_price=exit_price,
                            stop_loss=pos.stop_loss,
                            take_profit=pos.take_profit,
                            volume=pos.volume,
                            net_pnl=net_pnl,
                            gross_pnl=gross,
                            spread_cost=spread_c,
                            slippage_cost=slip_c,
                            commission_cost=comm,
                            swap_cost=pos.accumulated_swap,
                            exit_reason=reason,
                        )
                    )

        # 5. Đánh giá chiến lược để sinh tín hiệu mới
        result = self.strategy(snapshot, self.config, time_utc)
        candidate = result.candidate
        if candidate and candidate.candidate_id not in self.account.positions:
            if self.use_tiered_sizing:
                current_eq = self.account.get_equity(price_dict)
                tiered_engine = TieredCompoundRiskEngine(Decimal(str(self.initial_balance)))
                tiered_eval = tiered_engine.evaluate_budget(Decimal(str(max(0.0, current_eq))))
                risk_budget = float(tiered_eval.risk_budget)
                entry_ref = candidate.reference_entry if candidate.reference_entry > 0 else (effective_ask if candidate.side == "BUY" else effective_bid)
                risk_dist = abs(entry_ref - candidate.stop_loss)
                contract_size = self.account.get_contract_size(symbol)
                if "JPY" in symbol.upper():
                    loss_per_lot = (risk_dist * contract_size) / entry_ref
                else:
                    loss_per_lot = risk_dist * contract_size
                if loss_per_lot > 0:
                    raw_vol = risk_budget / loss_per_lot
                    volume = max(0.01, min(100.0, math.floor(raw_vol * 100.0) / 100.0))
                else:
                    volume = 0.01
            else:
                volume = 0.01  # Kích thước lot tiêu chuẩn (Canary baseline)
            entry_slip = self.slippage_model.calculate_slippage(symbol, "MARKET", volume)
            actual_entry = (effective_ask + entry_slip) if candidate.side == "BUY" else (effective_bid - entry_slip)

            if self.account.can_open(symbol, volume, actual_entry, price_dict):
                self.account.open_position(
                    position_id=candidate.candidate_id,
                    symbol=symbol,
                    side=candidate.side,
                    volume=volume,
                    entry_price=actual_entry,
                    time_utc=time_utc,
                    stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit,
                )

        # 6. Cập nhật số liệu Drawdown
        current_eq = self.account.get_equity(price_dict)
        if current_eq > self.peak_balance:
            self.peak_balance = current_eq
        dd = self.peak_balance - current_eq
        dd_pct = (dd / self.peak_balance * 100.0) if self.peak_balance > 0 else 0.0
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        if dd_pct > self.max_drawdown_pct:
            self.max_drawdown_pct = dd_pct

    def generate_report(self, initial_balance: Optional[float] = None) -> RealisticBacktestReport:
        init_bal = initial_balance if initial_balance is not None else self.initial_balance
        total = len(self.closed_trades)
        wins = sum(1 for t in self.closed_trades if t.net_pnl > 0)
        losses = sum(1 for t in self.closed_trades if t.net_pnl < 0)
        win_rate = (wins / total * 100.0) if total > 0 else 0.0

        gross_win = sum(t.net_pnl for t in self.closed_trades if t.net_pnl > 0)
        gross_loss = abs(sum(t.net_pnl for t in self.closed_trades if t.net_pnl < 0))
        pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

        return RealisticBacktestReport(
            initial_balance=init_bal,
            final_balance=self.account.balance,
            total_net_pnl=self.account.balance - init_bal,
            total_trades=total,
            winning_trades=wins,
            losing_trades=losses,
            win_rate_pct=win_rate,
            profit_factor=pf,
            max_drawdown_amount=self.max_drawdown,
            max_drawdown_pct=self.max_drawdown_pct,
            total_spread_cost=sum(t.spread_cost for t in self.closed_trades),
            total_slippage_cost=sum(t.slippage_cost for t in self.closed_trades),
            total_commission_cost=sum(t.commission_cost for t in self.closed_trades),
            total_swap_cost=sum(t.swap_cost for t in self.closed_trades),
            trades=tuple(self.closed_trades),
        )
