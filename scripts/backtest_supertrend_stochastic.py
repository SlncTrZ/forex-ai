"""Backtest Supertrend + Stochastic M5 Strategy on XAUUSD.
Wing: code | Topic: supertrend_stochastic_backtest | Updated: 2026-10-30 20:37
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def load_dataset(data_dir: Path, symbol: str = "XAUUSDm") -> pd.DataFrame:
    m5_path = data_dir / f"{symbol}_M5.parquet"
    if not m5_path.exists():
        # Fallback không có chữ m
        m5_path = data_dir / f"{symbol.removesuffix('m')}_M5.parquet"
    if not m5_path.exists():
        raise FileNotFoundError(f"Missing M5 parquet at {m5_path}")
    df = pd.read_parquet(m5_path)
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.set_index("dt").sort_index()
    return df


def calculate_supertrend(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 10, multiplier: float = 3.0
) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    tr = np.zeros(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))

    atr = np.zeros(n, dtype=np.float64)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    final_ub = np.zeros(n, dtype=np.float64)
    final_lb = np.zeros(n, dtype=np.float64)
    st_val = np.zeros(n, dtype=np.float64)
    trend = np.zeros(n, dtype=np.int32)

    for i in range(period, n):
        hl2 = (high[i] + low[i]) / 2.0
        basic_ub = hl2 + multiplier * atr[i]
        basic_lb = hl2 - multiplier * atr[i]

        if i == period:
            final_ub[i] = basic_ub
            final_lb[i] = basic_lb
            trend[i] = 1 if close[i] > hl2 else -1
            st_val[i] = final_lb[i] if trend[i] == 1 else final_ub[i]
            continue

        final_ub[i] = basic_ub if (basic_ub < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]) else final_ub[i - 1]
        final_lb[i] = basic_lb if (basic_lb > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]) else final_lb[i - 1]

        prev_t = trend[i - 1]
        if prev_t == -1 and close[i] > final_ub[i - 1]:
            cur_t = 1
        elif prev_t == 1 and close[i] < final_lb[i - 1]:
            cur_t = -1
        else:
            cur_t = prev_t

        trend[i] = cur_t
        st_val[i] = final_lb[i] if cur_t == 1 else final_ub[i]

    return trend, st_val


def calculate_stochastic(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, k_len: int = 7, d_len: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    stoch_k = np.full(n, 50.0, dtype=np.float64)
    for i in range(k_len - 1, n):
        wh = np.max(high[i - k_len + 1 : i + 1])
        wl = np.min(low[i - k_len + 1 : i + 1])
        diff = wh - wl
        stoch_k[i] = 100.0 * (close[i] - wl) / diff if diff > 1e-9 else 50.0

    stoch_d = np.full(n, 50.0, dtype=np.float64)
    for i in range(d_len - 1, n):
        stoch_d[i] = np.mean(stoch_k[i - d_len + 1 : i + 1])

    return stoch_k, stoch_d


def simulate(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    trend: np.ndarray,
    st_val: np.ndarray,
    stoch_k: np.ndarray,
    stoch_d: np.ndarray,
    hours_utc: np.ndarray,
    rr: float = 1.5,
    sl_buffer: float = 3.0,
    max_sl: float = 15.0,
    trigger_mode: str = "CROSS_DOWN",
    session_filter: str = "ALL",
    spread_usd: float = 0.25,
) -> dict[str, Any]:
    n = len(close)
    trades: list[float] = []
    pos: tuple[int, float, float, float] | None = None  # (side, entry, sl, tp)

    for i in range(50, n):
        # 1. Quản lý lệnh đang mở
        if pos is not None:
            side, entry, sl, tp = pos
            closed = False
            pnl = 0.0

            if side == 1:  # BUY
                if low[i] <= sl and high[i] >= tp:
                    pnl = sl - entry - spread_usd
                    closed = True
                elif low[i] <= sl:
                    pnl = sl - entry - spread_usd
                    closed = True
                elif high[i] >= tp:
                    pnl = tp - entry - spread_usd
                    closed = True
            else:  # SELL
                if high[i] >= sl and low[i] <= tp:
                    pnl = entry - sl - spread_usd
                    closed = True
                elif high[i] >= sl:
                    pnl = entry - sl - spread_usd
                    closed = True
                elif low[i] <= tp:
                    pnl = entry - tp - spread_usd
                    closed = True

            if closed:
                trades.append(pnl)
                pos = None

        if pos is not None:
            continue

        # 2. Bộ lọc phiên
        if session_filter == "AU_MY" and (hours_utc[i] < 6 or hours_utc[i] > 15):
            continue
        if hours_utc[i] in {21, 22}:
            continue

        # 3. Kích hoạt tín hiệu
        sig = 0
        if trend[i] == 1 and close[i] > st_val[i]:
            if trigger_mode == "CROSS_DOWN" and stoch_k[i] <= 20.0 and stoch_k[i - 1] > 20.0:
                sig = 1
            elif trigger_mode == "CROSS_UP" and stoch_k[i] >= 20.0 and stoch_k[i - 1] < 20.0:
                sig = 1
            elif trigger_mode == "K_CROSS_D" and stoch_k[i] > stoch_d[i] and stoch_k[i - 1] <= stoch_d[i - 1] and stoch_k[i] <= 30.0:
                sig = 1

        elif trend[i] == -1 and close[i] < st_val[i]:
            if trigger_mode == "CROSS_DOWN" and stoch_k[i] >= 80.0 and stoch_k[i - 1] < 80.0:
                sig = -1
            elif trigger_mode == "CROSS_UP" and stoch_k[i] <= 80.0 and stoch_k[i - 1] > 80.0:
                sig = -1
            elif trigger_mode == "K_CROSS_D" and stoch_k[i] < stoch_d[i] and stoch_k[i - 1] >= stoch_d[i - 1] and stoch_k[i] >= 70.0:
                sig = -1

        if sig == 1:
            entry = close[i] + spread_usd / 2.0
            sl = st_val[i] - sl_buffer
            risk = entry - sl
            if 1.0 <= risk <= max_sl:
                tp = entry + risk * rr
                pos = (1, entry, sl, tp)
        elif sig == -1:
            entry = close[i] - spread_usd / 2.0
            sl = st_val[i] + sl_buffer
            risk = sl - entry
            if 1.0 <= risk <= max_sl:
                tp = entry - risk * rr
                pos = (-1, entry, sl, tp)

    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_pnl": 0.0, "max_dd": 0.0, "avg_trade": 0.0}

    t = np.array(trades, dtype=np.float64)
    total = len(t)
    wins = np.sum(t > 0)
    wr = float(wins / total * 100.0)
    gw = float(np.sum(t[t > 0])) if np.any(t > 0) else 0.0
    gl = float(abs(np.sum(t[t < 0]))) if np.any(t < 0) else 1e-9
    pf = float(gw / gl) if gl > 0 else 0.0
    net_pnl = float(np.sum(t))
    cum = np.cumsum(t)
    dd = float(np.max(np.maximum.accumulate(cum) - cum))

    return {
        "trades": total,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 3),
        "net_pnl": round(net_pnl, 1),
        "max_dd": round(dd, 1),
        "avg_trade": round(net_pnl / total, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest Supertrend + Stochastic M5 Strategy")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Directory containing parquet files")
    parser.add_argument("--symbol", type=str, default="XAUUSDm", help="Trading symbol")
    args = parser.parse_args()

    df = load_dataset(args.data_dir, args.symbol)
    print(f"Loaded {len(df):,} bars for {args.symbol} from {df.index[0]} to {df.index[-1]}")

    c = df["close"].to_numpy(dtype=np.float64)
    h = df["high"].to_numpy(dtype=np.float64)
    l = df["low"].to_numpy(dtype=np.float64)
    hours_utc = df.index.hour.to_numpy()

    trend, st_val = calculate_supertrend(h, l, c, period=10, multiplier=3.0)
    stoch_k, stoch_d = calculate_stochastic(h, l, c, k_len=7, d_len=3)

    print("\n" + "=" * 90)
    print(f"MA TRAN BACKTEST SUPERTREND + STOCHASTIC ({args.symbol} M5)")
    print("=" * 90)
    header = f"{'Cau hinh':<46} | {'Lenh':<5} | {'WR':<5} | {'PF':<5} | {'Net PnL':<9} | {'MaxDD':<6} | {'Avg':<5}"
    print(header)
    print("-" * len(header))

    tests = [
        ("1. Goc: Cross Down 20 (ALL)  | RR 1:1.5", "CROSS_DOWN", 1.5, 999.0, "ALL"),
        ("2. Goc: Cross Down 20 (ALL)  | RR 1:2.0", "CROSS_DOWN", 2.0, 999.0, "ALL"),
        ("3. Goc: Cross Down 20 (Au-My)| RR 1:1.5", "CROSS_DOWN", 1.5, 999.0, "AU_MY"),
        ("4. Goc: Cross Down 20 (Au-My)| RR 1:2.0", "CROSS_DOWN", 2.0, 999.0, "AU_MY"),
        ("5. SL Cap $6: Cross Down 20  | RR 1:1.5", "CROSS_DOWN", 1.5, 6.0, "AU_MY"),
        ("6. SL Cap $6: Cross Down 20  | RR 1:2.0", "CROSS_DOWN", 2.0, 6.0, "AU_MY"),
        ("7. Turn-around: Cross Up 20  | RR 1:1.5", "CROSS_UP", 1.5, 999.0, "AU_MY"),
        ("8. Turn-around: Cross Up 20  | RR 1:2.0", "CROSS_UP", 2.0, 999.0, "AU_MY"),
        ("9. Stoch Cross K>D (Au-My)   | RR 1:1.5", "K_CROSS_D", 1.5, 999.0, "AU_MY"),
        ("10. Stoch Cross K>D (Au-My)  | RR 1:2.0", "K_CROSS_D", 2.0, 999.0, "AU_MY"),
        ("11. Refined: K>D + SL Cap $6.5| RR 1:5", "K_CROSS_D", 1.5, 6.5, "AU_MY"),
        ("12. Refined: K>D + SL Cap $6.5| RR 2:0", "K_CROSS_D", 2.0, 6.5, "AU_MY"),
    ]

    for label, trig, rr, max_sl, sess in tests:
        res = simulate(c, h, l, trend, st_val, stoch_k, stoch_d, hours_utc, rr=rr, sl_buffer=3.0, max_sl=max_sl, trigger_mode=trig, session_filter=sess)
        print(f"{label:<46} | {res['trades']:>5} | {res['win_rate']:>4.1f}% | {res['profit_factor']:>5.3f} | ${res['net_pnl']:>8.1f} | ${res['max_dd']:>6.1f} | ${res['avg_trade']:>4.2f}")


if __name__ == "__main__":
    main()
