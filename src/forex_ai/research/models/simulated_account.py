"""Simulated MT5 Broker Account — Margin tracking, Wednesday triple swap, and stop-out liquidation.
Wing: code | Topic: simulated_account | Updated: 2026-10-28 12:02
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

UTC = timezone.utc


@dataclass
class SimulatedPosition:
    position_id: str
    symbol: str
    side: str  # "BUY" | "SELL"
    volume: float  # Lots
    open_price: float
    open_time_utc: datetime
    stop_loss: float
    take_profit: float
    contract_size: float = 100_000.0
    accumulated_swap: float = 0.0
    last_swap_date: Optional[str] = None


@dataclass
class AccountSnapshot:
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level_pct: float
    open_positions_count: int


class SimulatedBrokerAccount:
    """Mô phỏng tài khoản MT5 chuẩn ECN với đòn bẩy, ký quỹ, swap x3 thứ Tư và ngưỡng stop-out."""

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        leverage: float = 100.0,
        commission_per_lot_round_turn: float = 7.0,
        margin_call_level_pct: float = 80.0,
        stop_out_level_pct: float = 30.0,
    ):
        self.balance: float = initial_balance
        self.leverage: float = leverage
        self.commission_rate: float = commission_per_lot_round_turn / 2.0  # Mỗi chiều mở/đóng
        self.margin_call_pct: float = margin_call_level_pct
        self.stop_out_pct: float = stop_out_level_pct
        self.positions: Dict[str, SimulatedPosition] = {}

    def get_contract_size(self, symbol: str) -> float:
        if "XAU" in symbol or "GOLD" in symbol:
            return 100.0  # 1 lot Vàng = 100 oz
        return 100_000.0  # Forex tiêu chuẩn

    def calculate_margin(self, symbol: str, volume: float, price: float) -> float:
        contract_size = self.get_contract_size(symbol)
        nominal_value = volume * contract_size * price
        return nominal_value / self.leverage

    def get_total_margin(self) -> float:
        return sum(
            self.calculate_margin(pos.symbol, pos.volume, pos.open_price)
            for pos in self.positions.values()
        )

    def get_floating_pnl(self, current_prices: dict[str, dict[str, float]]) -> float:
        """current_prices: {symbol: {'bid': float, 'ask': float}}"""
        total = 0.0
        for pos in self.positions.values():
            quotes = current_prices.get(pos.symbol)
            if not quotes:
                continue
            contract_size = pos.contract_size
            if pos.side == "BUY":
                # Đóng Buy tại giá Bid
                cur_price = quotes["bid"]
                pnl = (cur_price - pos.open_price) * pos.volume * contract_size
            else:
                # Đóng Sell tại giá Ask
                cur_price = quotes["ask"]
                pnl = (pos.open_price - cur_price) * pos.volume * contract_size
            total += pnl + pos.accumulated_swap
        return total

    def get_equity(self, current_prices: dict[str, dict[str, float]]) -> float:
        return self.balance + self.get_floating_pnl(current_prices)

    def get_snapshot(self, current_prices: dict[str, dict[str, float]]) -> AccountSnapshot:
        margin = self.get_total_margin()
        equity = self.get_equity(current_prices)
        free_margin = equity - margin
        margin_level = (equity / margin * 100.0) if margin > 0 else 99999.0
        return AccountSnapshot(
            balance=self.balance,
            equity=equity,
            margin=margin,
            free_margin=free_margin,
            margin_level_pct=margin_level,
            open_positions_count=len(self.positions),
        )

    def can_open(self, symbol: str, volume: float, price: float, current_prices: dict[str, dict[str, float]]) -> bool:
        required = self.calculate_margin(symbol, volume, price)
        equity = self.get_equity(current_prices)
        current_margin = self.get_total_margin()
        new_margin = current_margin + required
        if equity <= 0:
            return False
        new_margin_level = (equity / new_margin * 100.0) if new_margin > 0 else 0.0
        return new_margin_level >= self.margin_call_pct and (equity - new_margin) > 0

    def open_position(
        self,
        position_id: str,
        symbol: str,
        side: str,
        volume: float,
        entry_price: float,
        time_utc: datetime,
        stop_loss: float,
        take_profit: float,
    ) -> SimulatedPosition:
        contract_size = self.get_contract_size(symbol)
        pos = SimulatedPosition(
            position_id=position_id,
            symbol=symbol,
            side=side,
            volume=volume,
            open_price=entry_price,
            open_time_utc=time_utc,
            stop_loss=stop_loss,
            take_profit=take_profit,
            contract_size=contract_size,
        )
        # Trừ phí hoa hồng mở lệnh
        self.balance -= self.commission_rate * volume
        self.positions[position_id] = pos
        return pos

    def close_position(
        self,
        position_id: str,
        exit_price: float,
        time_utc: datetime,
    ) -> float:
        pos = self.positions.pop(position_id, None)
        if not pos:
            return 0.0
        contract_size = pos.contract_size
        if pos.side == "BUY":
            gross_pnl = (exit_price - pos.open_price) * pos.volume * contract_size
        else:
            gross_pnl = (pos.open_price - exit_price) * pos.volume * contract_size

        # Trừ commission đóng và cộng dồn swap
        net_pnl = gross_pnl - (self.commission_rate * pos.volume) + pos.accumulated_swap
        self.balance += net_pnl
        return net_pnl

    def process_daily_rollover(self, current_time_utc: datetime, swap_points: dict[str, dict[str, float]]) -> None:
        """Xử lý tính lãi qua đêm lúc 22:00 UTC. Nếu là đêm Thứ Tư: Nhân 3 (Triple Swap)."""
        t = current_time_utc.astimezone(UTC)
        if t.hour != 22:
            return
        date_str = t.strftime("%Y-%m-%d")

        # Wednesday overnight rollover has multiplier = 3
        is_wednesday = t.weekday() == 2  # 0=Mon, 1=Tue, 2=Wed
        swap_multiplier = 3.0 if is_wednesday else 1.0

        for pos in self.positions.values():
            if pos.last_swap_date == date_str:
                continue
            symbol_swaps = swap_points.get(pos.symbol, {"long": -5.0, "short": 1.0})
            rate_per_lot = symbol_swaps["long"] if pos.side == "BUY" else symbol_swaps["short"]
            swap_amount = rate_per_lot * pos.volume * swap_multiplier
            pos.accumulated_swap += swap_amount
            pos.last_swap_date = date_str

    def check_stop_out(self, current_prices: dict[str, dict[str, float]]) -> List[str]:
        """Kiểm tra và trả về danh sách position_id cần thanh lý cưỡng chế nếu Margin Level < Stop Out."""
        margin = self.get_total_margin()
        if margin <= 0:
            return []
        equity = self.get_equity(current_prices)
        margin_level = (equity / margin) * 100.0
        if margin_level >= self.stop_out_pct:
            return []

        # Sort positions by floating loss ascending (lỗ nhiều nhất lên đầu)
        losses = []
        for pid, pos in self.positions.items():
            quotes = current_prices.get(pos.symbol, {"bid": pos.open_price, "ask": pos.open_price})
            cur = quotes["bid"] if pos.side == "BUY" else quotes["ask"]
            pnl = (cur - pos.open_price if pos.side == "BUY" else pos.open_price - cur) * pos.volume * pos.contract_size
            losses.append((pid, pnl))
        losses.sort(key=lambda x: x[1])
        return [item[0] for item in losses]
