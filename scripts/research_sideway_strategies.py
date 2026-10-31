"""Research & Empirical Testing: Sideway / Mean-Reverting Strategies on .152.
Objective: Harvest alpha specifically when Regime == SIDEWAY, turning Choppy periods into profit.
Base Capital V0 = $1,000 USD (Mandatory Level Sizing Protocol).
Asset: XAUUSD M5 (100,000 bars real Exness tick data).
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd

PARQ = Path.home() / "fx-data" / "parquet"

df5 = pd.read_parquet(PARQ / "XAUUSDm_M5.parquet")
df5["dt"] = pd.to_datetime(df5["dt"])
df5 = df5.set_index("dt").sort_index()

df_h1 = pd.read_parquet(PARQ / "XAUUSDm_H1.parquet")
df_h1["dt"] = pd.to_datetime(df_h1["dt"])
df_h1 = df_h1.set_index("dt").sort_index()

c = df5["close"].to_numpy(dtype=np.float64)
h = df5["high"].to_numpy(dtype=np.float64)
l = df5["low"].to_numpy(dtype=np.float64)
o = df5["open"].to_numpy(dtype=np.float64)
v = df5["tick_volume"].to_numpy(dtype=np.float64)
dts = df5.index
hours_utc = dts.hour.to_numpy()
n = len(c)

print(f"Loaded {n:,} M5 bars.")

# 1. H1 Regime Classifier (ADX + EMA Separation & Slope)
h1_c = df_h1["close"].to_numpy(dtype=np.float64)
h1_h = df_h1["high"].to_numpy(dtype=np.float64)
h1_l = df_h1["low"].to_numpy(dtype=np.float64)
n_h1 = len(h1_c)

tr_h1 = np.zeros(n_h1)
for i in range(1, n_h1):
    tr_h1[i] = max(h1_h[i] - h1_l[i], abs(h1_h[i] - h1_c[i-1]), abs(h1_l[i] - h1_c[i-1]))
atr_h1 = pd.Series(tr_h1).rolling(14).mean().to_numpy()

plus_dm = np.zeros(n_h1)
minus_dm = np.zeros(n_h1)
for i in range(1, n_h1):
    up = h1_h[i] - h1_h[i-1]
    dn = h1_l[i-1] - h1_l[i]
    if up > dn and up > 0: plus_dm[i] = up
    if dn > up and dn > 0: minus_dm[i] = dn

tr_smooth = pd.Series(tr_h1).ewm(span=27).mean().to_numpy() # RMA 14
p_dm_smooth = pd.Series(plus_dm).ewm(span=27).mean().to_numpy()
m_dm_smooth = pd.Series(minus_dm).ewm(span=27).mean().to_numpy()

plus_di = np.where(tr_smooth > 0, 100.0 * p_dm_smooth / tr_smooth, 0.0)
minus_di = np.where(tr_smooth > 0, 100.0 * m_dm_smooth / tr_smooth, 0.0)
di_sum = plus_di + minus_di
dx = np.where(di_sum > 0, 100.0 * np.abs(plus_di - minus_di) / di_sum, 0.0)
adx_h1 = pd.Series(dx).ewm(span=27).mean().to_numpy()

h1_ema50 = pd.Series(h1_c).ewm(span=50).mean().to_numpy()
h1_ema200 = pd.Series(h1_c).ewm(span=200).mean().to_numpy()

# Classify H1 Regime: UPTREND, DOWNTREND, SIDEWAY
h1_regime_series = pd.Series("SIDEWAY", index=df_h1.index)
for i in range(50, n_h1):
    if adx_h1[i] >= 20.0 and h1_ema50[i] > h1_ema200[i] and plus_di[i] > minus_di[i]:
        h1_regime_series.iloc[i] = "UPTREND"
    elif adx_h1[i] >= 20.0 and h1_ema50[i] < h1_ema200[i] and minus_di[i] > plus_di[i]:
        h1_regime_series.iloc[i] = "DOWNTREND"
    else:
        h1_regime_series.iloc[i] = "SIDEWAY"

m5_regime = h1_regime_series.reindex(df5.index, method="ffill").fillna("SIDEWAY").to_numpy()
regime_counts = pd.Series(m5_regime).value_counts()
print("\nPhan bo trang thai thi truong (Regime Distribution on M5):")
for reg, cnt in regime_counts.items():
    print(f" - {reg:<12}: {cnt:>6,} bars ({cnt/n*100:>5.1f}%)")

# 2. Indicators on M5 for Sideway Strategies
tr_m5 = np.zeros(n)
for i in range(1, n): tr_m5[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
atr_m5 = pd.Series(tr_m5).rolling(14).mean().to_numpy()

sma20 = pd.Series(c).rolling(20).mean().to_numpy()
std20 = pd.Series(c).rolling(20).std().to_numpy()
bb_upper = sma20 + 2.0 * std20
bb_lower = sma20 - 2.0 * std20

diff_c = pd.Series(c).diff()
gain14 = diff_c.clip(lower=0).rolling(14).mean()
loss14 = (-diff_c.clip(upper=0)).rolling(14).mean()
rsi14 = (100 - (100 / (1 + (gain14 / loss14.replace(0, np.nan))))).fillna(50.0).to_numpy()

stoch_k = np.full(n, 50.0)
for i in range(6, n):
    wh = np.max(h[i-6 : i+1]); wl = np.min(l[i-6 : i+1]); diff = wh - wl
    stoch_k[i] = 100.0 * (c[i] - wl) / diff if diff > 1e-9 else 50.0
stoch_d = np.full(n, 50.0)
for i in range(2, n): stoch_d[i] = np.mean(stoch_k[i-2 : i+1])

swing_h20 = pd.Series(h).rolling(20).max().shift(1).to_numpy()
swing_l20 = pd.Series(l).rolling(20).min().shift(1).to_numpy()

print("Precompute indicators hoan tat.")

# 3. Design 3 Sideway Strategies
# Strategy 1: Bollinger Boundary Fade + RSI Extreme (BB FADE)
sig_bb_fade = np.zeros(n, dtype=int); sl_bb_fade = np.zeros(n); tp_bb_fade = np.zeros(n)
for i in range(20, n):
    if m5_regime[i] == "SIDEWAY":
        if l[i] <= bb_lower[i] and c[i] > bb_lower[i] and rsi14[i] <= 35.0:
            sig_bb_fade[i] = 1
            sl_bb_fade[i] = l[i] - 1.2
            tp_bb_fade[i] = sma20[i]
        elif h[i] >= bb_upper[i] and c[i] < bb_upper[i] and rsi14[i] >= 65.0:
            sig_bb_fade[i] = -1
            sl_bb_fade[i] = h[i] + 1.2
            tp_bb_fade[i] = sma20[i]

# Strategy 2: Liquidity Trap / False Breakout Fade (TURTLE SOUP FADE)
sig_fakeout = np.zeros(n, dtype=int); sl_fakeout = np.zeros(n); tp_fakeout = np.zeros(n)
for i in range(20, n):
    if m5_regime[i] == "SIDEWAY":
        if l[i] < swing_l20[i] and c[i] > swing_l20[i] and c[i] > o[i]:
            sig_fakeout[i] = 1
            sl_fakeout[i] = l[i] - 1.2
            risk = c[i] - sl_fakeout[i]
            tp_fakeout[i] = c[i] + 1.8 * risk
        elif h[i] > swing_h20[i] and c[i] < swing_h20[i] and c[i] < o[i]:
            sig_fakeout[i] = -1
            sl_fakeout[i] = h[i] + 1.2
            risk = sl_fakeout[i] - c[i]
            tp_fakeout[i] = c[i] - 1.8 * risk

# Strategy 3: Stochastic Rebound at Range Support/Resistance (STOCH RANGE BOUNCE)
sig_stoch_range = np.zeros(n, dtype=int); sl_stoch_range = np.zeros(n); tp_stoch_range = np.zeros(n)
for i in range(20, n):
    if m5_regime[i] == "SIDEWAY":
        range_h = swing_h20[i]
        range_l = swing_l20[i]
        rng = range_h - range_l
        if rng > 3.0:
            if c[i] <= range_l + 0.25 * rng and stoch_k[i] > stoch_d[i] and stoch_k[i-1] <= stoch_d[i-1] and stoch_k[i] <= 30.0:
                sig_stoch_range[i] = 1
                sl_stoch_range[i] = range_l - 1.5
                tp_stoch_range[i] = range_h - 0.5 * rng
            elif c[i] >= range_h - 0.25 * rng and stoch_k[i] < stoch_d[i] and stoch_k[i-1] >= stoch_d[i-1] and stoch_k[i] >= 70.0:
                sig_stoch_range[i] = -1
                sl_stoch_range[i] = range_h + 1.5
                tp_stoch_range[i] = range_l + 0.5 * rng

SIDEWAY_STRATS = {
    "1. BB Mean Reversion Fade (SMA20 Target)": (sig_bb_fade, sl_bb_fade, tp_bb_fade),
    "2. Liquidity Trap / False Breakout Fade": (sig_fakeout, sl_fakeout, tp_fakeout),
    "3. Stoch Range Support/Resistance Bounce": (sig_stoch_range, sl_stoch_range, tp_stoch_range),
}

# 4. Backtest Engine with Mandatory Tiered Compound Level Sizing (V0 = $1,000)
def backtest_sideway(strat_name, sig_arr, sl_arr, tp_arr, session_filter="AU_MY", v0=1000.0, spread=0.25):
    equity = v0
    peak = equity
    max_dd = 0.0
    trades = []
    pos = None

    for i in range(50, n):
        if pos is not None:
            side, entry, sl, tp, lot = pos
            closed = False
            pnl = 0.0
            if side == 1:
                if l[i] <= sl and h[i] >= tp:
                    pnl = (sl - entry - spread) * lot * 100.0; closed = True
                elif l[i] <= sl:
                    pnl = (sl - entry - spread) * lot * 100.0; closed = True
                elif h[i] >= tp:
                    pnl = (tp - entry - spread) * lot * 100.0; closed = True
            else:
                if h[i] >= sl and l[i] <= tp:
                    pnl = (entry - sl - spread) * lot * 100.0; closed = True
                elif h[i] >= sl:
                    pnl = (entry - sl - spread) * lot * 100.0; closed = True
                elif l[i] <= tp:
                    pnl = (entry - tp - spread) * lot * 100.0; closed = True

            if closed:
                equity += pnl
                if equity > peak: peak = equity
                dd = (peak - equity) / peak
                if dd > max_dd: max_dd = dd
                trades.append(pnl)
                pos = None

        if pos is not None:
            continue

        if session_filter == "AU_MY" and (hours_utc[i] < 6 or hours_utc[i] > 16):
            continue
        if hours_utc[i] in {21, 22}:
            continue

        s = sig_arr[i]
        if s != 0:
            entry = (c[i] + spread / 2.0) if s == 1 else (c[i] - spread / 2.0)
            sl = sl_arr[i]
            tp = tp_arr[i]
            risk_dist = abs(entry - sl)
            reward_dist = abs(tp - entry)

            if 1.0 <= risk_dist <= 5.0 and reward_dist >= 1.0 * risk_dist:
                if equity >= v0:
                    lvl = int(math.floor(math.log(max(1.0, equity) / v0) / math.log(1.1) + 1e-9))
                else:
                    lvl = -int(math.ceil(math.log(max(1.0, equity) / v0) / math.log(0.9) - 1e-9))

                risk_pct = 0.03 if lvl <= 17 else (0.02 if lvl <= 50 else 0.01)
                if lvl < 0:
                    risk_pct = 0.02 if lvl >= -2 else (0.01 if lvl >= -5 else 0.005)

                risk_budget = equity * risk_pct
                raw_lot = risk_budget / (risk_dist * 100.0)
                lot = max(0.01, min(5.0, round(raw_lot, 2)))
                pos = (s, entry, sl, tp, lot)

    if not trades:
        return None

    t = np.array(trades)
    tot = len(t); wins = int(np.sum(t > 0))
    wr = wins / tot * 100.0
    gw = float(np.sum(t[t > 0])) if wins > 0 else 0.0
    gl = float(abs(np.sum(t[t < 0]))) if (tot - wins) > 0 else 1e-9
    pf = gw / gl if gl > 0 else 0.0
    net_pnl = equity - v0
    roi = (net_pnl / v0) * 100.0

    if equity >= v0:
        final_lvl = int(math.floor(math.log(max(1.0, equity) / v0) / math.log(1.1) + 1e-9))
    else:
        final_lvl = -int(math.ceil(math.log(max(1.0, equity) / v0) / math.log(0.9) - 1e-9))

    return {
        "strategy": strat_name,
        "final_equity": round(equity, 2),
        "net_pnl": round(net_pnl, 2),
        "roi_pct": round(roi, 1),
        "final_level": final_lvl,
        "trades": tot,
        "wins": wins,
        "losses": tot - wins,
        "wr": round(wr, 1),
        "pf": round(pf, 3),
        "max_dd_pct": round(max_dd * 100.0, 1),
    }

print("\n" + "=" * 115)
print("KET QUA BACKTEST THUC TE CAC CHIEN LUOC SIDEWAY TREN XAUUSD M5 (.152 - 100,000 BARS):")
print(f"Von khoi tao: $1,000 USD | Quy chuan Tiered Compound Level Sizing | Chi chay khi Regime == SIDEWAY")
print("=" * 115)
print(f"{'Chien Luoc Sideway':<42} | {'Trades':<6} | {'WR':<6} | {'PF':<6} | {'Net PnL ($)':<12} | {'ROI':<8} | {'Level':<8} | {'MaxDD':<6}")
print("-" * 115)

for s_name, (sig, sl, tp) in SIDEWAY_STRATS.items():
    res = backtest_sideway(s_name, sig, sl, tp, session_filter="AU_MY", v0=1000.0)
    if res:
        pnl_s = f"+${res['net_pnl']:,.2f}" if res['net_pnl'] > 0 else f"-${abs(res['net_pnl']):,.2f}"
        print(f"{res['strategy']:<42} | {res['trades']:>4}   | {res['wr']:>4.1f}% | {res['pf']:>5.3f} | {pnl_s:>12} | {res['roi_pct']:>6.1f}% | Level {res['final_level']:>2} | {res['max_dd_pct']:>4.1f}%")

if __name__ == "__main__":
    pass
