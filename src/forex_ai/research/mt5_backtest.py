"""MT5-backed deterministic M5 strategy backtest.

All candles, broker contract metadata, historical spread and profit conversion
come from the configured MT5 terminal.  The execution model deliberately
resolves a bar touching both stop and target as a stop first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any

import pandas as pd

from forex_ai.mt5.client import MT5Client

UTC = timezone.utc
TIMEFRAME_M5 = 5
TIMEFRAME_H1 = 60


@dataclass(frozen=True)
class BacktestRequest:
    symbol: str
    h1_ema_fast: int
    h1_ema_slow: int
    rsi_threshold: float
    m5_ema: int
    sl_distance: float
    rr_ratio: float
    trades_to_levelup: int
    initial_equity: float
    session_mode: str
    enable_dca: bool
    dca_step: float
    dca_recovery: float
    max_dca_layers: int = 3
    history_days: int = 365


def _ema(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi(values: pd.Series, period: int = 14) -> pd.Series:
    delta = values.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_down = down.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return 100 - (100 / (1 + avg_up / avg_down.replace(0, float("nan"))))


def _to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        raise ValueError("MT5 returned no historical candles for the requested period")
    frame = pd.DataFrame(rows)
    frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    return frame.drop_duplicates("time").sort_values("time").set_index("time")


def _h1_from_mt5_m5(m5: pd.DataFrame) -> pd.DataFrame:
    """Derive closed H1 OHLC from MT5 M5 history only when MT5 H1 is absent."""
    hourly = m5.resample("1h", label="left", closed="left").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), tick_volume=("tick_volume", "sum"),
    ).dropna()
    return hourly


def _floor_volume(volume: float, minimum: float, maximum: float, step: float) -> float:
    if volume < minimum:
        return 0.0
    bounded = min(volume, maximum)
    units = math.floor((bounded - minimum + 1e-12) / step)
    return round(minimum + units * step, 8)


def _profit(client: MT5Client, *, side: int, symbol: str, volume: float, entry: float, exit_price: float) -> float:
    # MT5 constants are 0=BUY and 1=SELL; use its own contract/currency conversion.
    result = client.order_calc_profit(0 if side == 1 else 1, symbol, volume, entry, exit_price)
    if result is None:
        raise RuntimeError("MT5 order_calc_profit returned None")
    return float(result)


def run_mt5_backtest(client: MT5Client, request: BacktestRequest) -> dict[str, Any]:
    """Run one bar-based backtest using broker-history and broker PnL conversion."""
    if not (2 <= request.h1_ema_fast < request.h1_ema_slow <= 500):
        raise ValueError("H1 EMA must satisfy 2 <= fast < slow <= 500")
    if not 50 < request.rsi_threshold < 100:
        raise ValueError("RSI threshold must be in (50, 100); values such as 30 create overlapping BUY/SELL rules")
    if request.sl_distance <= 0 or request.rr_ratio <= 0 or request.initial_equity <= 0:
        raise ValueError("SL, RR and initial equity must be positive")
    if request.trades_to_levelup < 1 or request.max_dca_layers not in (1, 2, 3):
        raise ValueError("Trade-to-LevelUp must be >= 1 and DCA layers must be 1–3")

    info = client.symbol_info(request.symbol)
    if not info:
        raise ValueError(f"MT5 symbol unavailable: {request.symbol}")
    point = float(info.get("point") or 0)
    volume_min = float(info.get("volume_min") or 0)
    volume_max = float(info.get("volume_max") or 0)
    volume_step = float(info.get("volume_step") or 0)
    if point <= 0 or volume_min <= 0 or volume_max <= 0 or volume_step <= 0:
        raise ValueError("MT5 returned invalid symbol volume/point metadata")

    end = datetime.now(UTC)
    # Warmup covers the longest H1 EMA before requested evaluation range.
    start = end - timedelta(days=request.history_days + max(30, request.h1_ema_slow // 6))
    m5 = _to_frame(client.bars_range(request.symbol, TIMEFRAME_M5, start.timestamp(), end.timestamp()))
    h1_rows = client.bars_range(request.symbol, TIMEFRAME_H1, start.timestamp(), end.timestamp())
    h1_is_derived = not h1_rows
    h1 = _h1_from_mt5_m5(m5) if h1_is_derived else _to_frame(h1_rows)
    if len(m5) < 1_000 or len(h1) < request.h1_ema_slow + 20:
        raise ValueError("MT5 history is insufficient for the selected indicators and period")

    h1["fast"] = _ema(h1["close"], request.h1_ema_fast)
    h1["slow"] = _ema(h1["close"], request.h1_ema_slow)
    h1["bias"] = 0
    h1.loc[(h1.close > h1.fast) & (h1.fast > h1.slow), "bias"] = 1
    h1.loc[(h1.close < h1.fast) & (h1.fast < h1.slow), "bias"] = -1
    # H1 value is usable only after that H1 bar closes; shift one full H1 candle.
    m5["bias"] = h1["bias"].reindex(m5.index, method="ffill").shift(12).fillna(0).astype(int)
    m5["ema"] = _ema(m5.close, request.m5_ema)
    m5["rsi"] = _rsi(m5.close)
    m5 = m5.iloc[request.h1_ema_slow * 12:].copy()

    target_move = request.sl_distance * request.rr_ratio
    probe_price = float(m5.close.iloc[0])
    probe_profit = abs(_profit(client, side=1, symbol=request.symbol, volume=volume_min, entry=probe_price, exit_price=probe_price + target_move))
    if probe_profit <= 0:
        raise ValueError("MT5 cannot calculate target profit for this symbol")
    target_fraction = 1.10 ** (1 / request.trades_to_levelup) - 1

    equity = float(request.initial_equity)
    peak = equity
    max_drawdown = 0.0
    max_volume = 0.0
    wins = losses = 0
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = [{"time": m5.index[0].isoformat(), "equity": equity}]
    basket: dict[str, Any] | None = None

    def close_basket(when: pd.Timestamp, exit_price: float, reason: str) -> None:
        nonlocal basket, equity, peak, max_drawdown, wins, losses
        assert basket is not None
        pnl = sum(_profit(client, side=basket["side"], symbol=request.symbol, volume=x["volume"], entry=x["entry"], exit_price=exit_price) for x in basket["layers"])
        equity += pnl
        if pnl >= 0:
            wins += 1
        else:
            losses += 1
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
        trades.append({"time": when.isoformat(), "side": "BUY" if basket["side"] == 1 else "SELL", "layers": len(basket["layers"]), "entry": round(basket["layers"][0]["entry"], 5), "exit": round(exit_price, 5), "volume": round(sum(x["volume"] for x in basket["layers"]), 4), "pnl": round(pnl, 2), "reason": reason})
        curve.append({"time": when.isoformat(), "equity": round(equity, 2)})
        basket = None

    rows = m5.reset_index()
    for index in range(1, len(rows)):
        row = rows.iloc[index]
        timestamp = row["time"]
        if basket:
            side = basket["side"]
            stop = basket["hard_stop"]
            target = basket["target"]
            # Conservative: when both thresholds are reachable in one OHLC bar, stop wins.
            stop_hit = float(row.low) <= stop if side == 1 else float(row.high) >= stop
            target_hit = float(row.high) >= target if side == 1 else float(row.low) <= target
            if stop_hit:
                close_basket(timestamp, stop, "basket_stop")
            elif target_hit:
                close_basket(timestamp, target, "target")
            elif request.enable_dca and len(basket["layers"]) < request.max_dca_layers:
                trigger = basket["next_dca"]
                crossed = float(row.low) <= trigger if side == 1 else float(row.high) >= trigger
                if crossed:
                    basket["layers"].append({"entry": trigger, "volume": basket["base_volume"]})
                    basket["next_dca"] = trigger - request.dca_step if side == 1 else trigger + request.dca_step
                    weighted = sum(x["entry"] * x["volume"] for x in basket["layers"]) / sum(x["volume"] for x in basket["layers"])
                    basket["target"] = weighted + request.dca_recovery if side == 1 else weighted - request.dca_recovery

        hour_vn = (timestamp + pd.Timedelta(hours=7)).hour
        allowed = request.session_mode == "24_5" or 13 <= hour_vn < 22
        if basket is not None or not allowed:
            continue
        previous = rows.iloc[index - 1]
        buy = row.bias == 1 and row.rsi > request.rsi_threshold and previous.close <= previous.ema and row.close > row.ema
        sell = row.bias == -1 and row.rsi < 100 - request.rsi_threshold and previous.close >= previous.ema and row.close < row.ema
        side = 1 if buy else -1 if sell else 0
        if not side:
            continue
        entry = float(row.close)
        base_volume = _floor_volume(equity * target_fraction * volume_min / probe_profit, volume_min, volume_max, volume_step)
        if base_volume <= 0:
            continue
        max_volume = max(max_volume, base_volume)
        basket = {"side": side, "layers": [{"entry": entry, "volume": base_volume}], "base_volume": base_volume, "target": entry + target_move if side == 1 else entry - target_move, "hard_stop": entry - request.sl_distance if side == 1 else entry + request.sl_distance, "next_dca": entry - request.dca_step if side == 1 else entry + request.dca_step}

    if basket:
        close_basket(m5.index[-1], float(m5.close.iloc[-1]), "end_of_history")
    completed = wins + losses
    source = "MT5 M5/H1 broker history + MT5 order_calc_profit"
    if h1_is_derived:
        source = "MT5 M5 broker history (H1 OHLC derived from closed M5) + MT5 order_calc_profit"
    return {"source": source, "symbol": request.symbol, "period": {"from": m5.index[0].isoformat(), "to": m5.index[-1].isoformat(), "m5_bars": len(m5), "h1_bars": len(h1), "h1_derived_from_mt5_m5": h1_is_derived}, "contract": {key: info.get(key) for key in ("name", "currency_profit", "trade_contract_size", "trade_tick_size", "point", "volume_min", "volume_max", "volume_step")}, "metrics": {"initial_equity": request.initial_equity, "final_equity": round(equity, 2), "return_pct": round((equity / request.initial_equity - 1) * 100, 2), "trades": completed, "winrate": round(100 * wins / completed, 2) if completed else 0.0, "max_drawdown_pct": round(max_drawdown * 100, 2), "max_volume": max_volume}, "equity_curve": curve[-300:], "trades": trades[-100:]}
