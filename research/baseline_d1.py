"""Research harness (P1) — honest daily baseline: TSMOM family vs controls.

Muc tieu: khong tim "EA tot" ma do xem ho trend-following co edge THAT tren
du lieu 12+ nam hay khong. Moi ket qua phai vuot 3 chot:
  1) buy & hold cung symbol
  2) random-entry control cung sizing
  3) PBO (probability of backtest overfitting) < 0.5

Run:
    python research/baseline_d1.py --data-dir ~/fx-data/parquet --out ~/fx-results
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Chi phi vong (bps): spread + slippage, uoc luong tu broker Exness demo/real.
SYMBOL_COST_BPS = {
    "XAUUSD": 6.0,
    "EURUSD": 0.9,
    "USDJPY": 1.0,
    "BTCUSD": 8.0,
}


def load_d1(data_dir: Path) -> dict[str, pd.DataFrame]:
    """Doc parquet D1, chuan hoa index UTC-naive, tra ve dict symbol -> OHLC."""
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(data_dir.glob("*_D1.parquet")):
        sym = path.name.split("_")[0].removesuffix("m")  # broker suffix: XAUUSDm -> XAUUSD
        df = pd.read_parquet(path)
        df["dt"] = pd.to_datetime(df["dt"], utc=True).dt.tz_localize(None)
        df = df.set_index("dt").sort_index()
        df = df[["open", "high", "low", "close"]].astype(float)
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna()
        if len(df) < 500:
            continue
        out[sym] = df
    return out


@dataclass
class Metrics:
    name: str
    cagr_pct: float
    vol_pct: float
    sharpe: float
    sortino: float
    max_dd_pct: float
    calmar: float
    hit_rate_pct: float
    turnover_per_year: float
    years: float
    start: str
    end: str


def bars_per_year(index) -> float:
    """So bar/nam thuc te cua du lieu (D1 FX ~309, crypto 365)."""
    if len(index) < 2:
        return float(TRADING_DAYS)
    years = (index[-1] - index[0]).days / 365.25
    return float(len(index) / years) if years > 0 else float(TRADING_DAYS)


def metrics(name: str, ret: pd.Series, turnover: pd.Series | None = None) -> Metrics:
    ret = ret.dropna()
    ret = ret[~ret.index.duplicated(keep="last")].sort_index()
    if ret.empty:
        raise ValueError(f"empty returns for {name}")
    equity = (1.0 + ret).cumprod()
    # nam theo LICH, va so bar/nam theo DU LIEU THUC TE (D1 broker co bar Chu nhat
    # ~309 bar/nam voi FX, 365 voi crypto) — dung 252 se thoi phong Sharpe ~10%.
    years = (ret.index[-1] - ret.index[0]).days / 365.25
    bpy = len(ret) / years if years > 0 else float(TRADING_DAYS)
    cagr = equity.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else 0.0
    vol = ret.std(ddof=1) * math.sqrt(bpy)
    sharpe = (ret.mean() / ret.std(ddof=1) * math.sqrt(bpy)) if ret.std(ddof=1) > 0 else 0.0
    downside = ret[ret < 0].std(ddof=1)
    sortino = (ret.mean() / downside * math.sqrt(bpy)) if downside and downside > 0 else 0.0
    peak = equity.cummax()
    dd = (equity / peak - 1.0).min()
    turnover_py = float(turnover.abs().sum() / years) if turnover is not None and years > 0 else 0.0
    return Metrics(
        name=name,
        cagr_pct=round(cagr * 100, 2),
        vol_pct=round(vol * 100, 2),
        sharpe=round(float(sharpe), 3),
        sortino=round(float(sortino), 3),
        max_dd_pct=round(float(dd) * 100, 2),
        calmar=round(float(cagr / abs(dd)) if dd < 0 else 0.0, 2),
        hit_rate_pct=round(float((ret > 0).mean()) * 100, 2),
        turnover_per_year=round(turnover_py, 1),
        years=round(years, 2),
        start=str(ret.index[0].date()),
        end=str(ret.index[-1].date()),
    )


def realized_vol(close: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised vol cua log-return."""
    lr = np.log(close).diff()
    return lr.rolling(window, min_periods=window).std() * math.sqrt(TRADING_DAYS)


def tsmom_signal(close: pd.Series, lookback: int) -> pd.Series:
    """+1/-1 theo dau momentum lookback ngay. 0 khi thieu du lieu."""
    mom = close / close.shift(lookback) - 1.0
    return np.sign(mom)


def build_positions(
    close: pd.Series,
    lookback: int,
    target_vol: float,
    vol_floor: float = 0.02,
    max_leverage: float = 3.0,
) -> pd.Series:
    """Position size (notional / equity) theo TSMOM + vol targeting."""
    sig = tsmom_signal(close, lookback)
    rv = realized_vol(close, 20)
    lev = (target_vol / rv.clip(lower=vol_floor)).clip(upper=max_leverage)
    pos = (sig * lev).fillna(0.0)
    return pos


def backtest_symbol(
    df: pd.DataFrame,
    lookback: int,
    target_vol: float,
    rebalance: str,
    cost_bps: float,
) -> tuple[pd.Series, pd.Series]:
    """Tra ve (daily returns, turnover) cho 1 symbol. Khong look-ahead.

    Signal tinh tren close cua ngay t. Vi the giu tu open t+1 -> open t+2.
    Don gian hoa: dung close-to-close return nhung shift position 1 ngay,
    tuong duong vao lenh tai close t+1 (tre hon so voi thuc te -> bao thu).
    """
    close = df["close"]
    pos = build_positions(close, lookback, target_vol)
    pos = _rebalance_hold(pos, rebalance)

    ret = close.pct_change().fillna(0.0)
    held = pos.shift(1).fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    cost = turnover * (cost_bps / 1e4) * 2.0  # vao + ra
    net = held * ret - cost
    return net, turnover


def portfolio_returns(
    data: dict[str, pd.DataFrame],
    lookback: int,
    target_vol: float,
    rebalance: str,
    weight_scheme: str = "equal",
) -> tuple[pd.Series, pd.Series, dict[str, pd.Series]]:
    per_symbol: dict[str, pd.Series] = {}
    per_turnover: dict[str, pd.Series] = {}
    for sym, df in data.items():
        cost = SYMBOL_COST_BPS.get(sym, 5.0)
        ret, to = backtest_symbol(df, lookback, target_vol, rebalance, cost)
        per_symbol[sym] = ret
        per_turnover[sym] = to

    frame = pd.DataFrame(per_symbol).sort_index()
    turnover = pd.DataFrame(per_turnover).sort_index().reindex(frame.index)
    if weight_scheme == "equal":
        w = pd.Series(1.0 / frame.shape[1], index=frame.columns)
    else:
        w = pd.Series(1.0 / frame.shape[1], index=frame.columns)
    port = (frame * w).sum(axis=1, min_count=frame.shape[1]).dropna()
    port_to = (turnover * w).sum(axis=1, min_count=frame.shape[1]).reindex(port.index)
    return port, port_to, frame


def xsmom_returns(
    data: dict[str, pd.DataFrame],
    lookback: int,
    rebalance: str,
    target_vol: float,
) -> tuple[pd.Series, pd.Series]:
    """Cross-sectional momentum: long top half, short bottom half cua basket.

    Day la bien the co support hoc thuat manh hon (Moskowitz-Ooi-Pedersen 2012):
    xep hang theo momentum roi long/short tuong doi, khong phai doan tung symbol.
    """
    closes = pd.DataFrame({s: df["close"] for s, df in data.items()}).sort_index().ffill()
    mom = closes / closes.shift(lookback) - 1.0
    vol = pd.DataFrame({s: realized_vol(df["close"], 20) for s, df in data.items()}).sort_index()

    ranks = mom.rank(axis=1, method="average", pct=True)
    raw = (ranks - 0.5) * 2.0
    full = ranks.notna().sum(axis=1) == closes.shape[1]
    raw = raw.where(full, np.nan)

    # risk budget: moi symbol dong gop target_vol/sqrt(n), khong chuan hoa gross
    # (chuan hoa gross se vo hieu hoa vol targeting - tung cho ket qua giong het nhau)
    n = closes.shape[1]
    lev = (target_vol / math.sqrt(n)) / vol.clip(lower=0.02)
    w = (raw * lev).fillna(0.0)
    w = w.apply(lambda col: _rebalance_hold(col, rebalance))

    rets = closes.pct_change().fillna(0.0)
    held = w.shift(1).fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    cost_rate = pd.Series({s: SYMBOL_COST_BPS.get(s, 5.0) / 1e4 for s in held.columns})
    cost = (turnover * cost_rate).sum(axis=1) * 2.0
    port = (held * rets).sum(axis=1) - cost
    return port.loc[full.fillna(False).to_numpy()], turnover.sum(axis=1)


def buy_and_hold(data: dict[str, pd.DataFrame], target_vol: float) -> tuple[pd.Series, pd.Series]:
    rets = {}
    tos = {}
    for sym, df in data.items():
        close = df["close"]
        rv = realized_vol(close, 20)
        lev = (target_vol / rv.clip(lower=0.02)).clip(upper=3.0)
        r = close.pct_change().fillna(0.0)
        held = lev.shift(1).fillna(0.0)
        to = held.diff().abs().fillna(0.0)
        rets[sym] = held * r - to * (SYMBOL_COST_BPS.get(sym, 5.0) / 1e4) * 2.0
        tos[sym] = to
    w = 1.0 / len(rets)
    port = (pd.DataFrame(rets).sort_index() * w).sum(axis=1, min_count=len(rets)).dropna()
    port_to = (pd.DataFrame(tos).sort_index() * w).sum(axis=1, min_count=len(rets)).reindex(port.index)
    return port, port_to


def _rebalance_hold(pos: pd.Series, rebalance: str) -> pd.Series:
    """Giu position co dinh giua cac ky rebalance (khong doi moi ngay)."""
    if rebalance == "daily":
        return pos
    mask = pd.Series(False, index=pos.index)
    if rebalance == "weekly":
        mask[pos.index.dayofweek == 4] = True
    elif rebalance == "monthly":
        periods = pd.Series(pos.index, index=pos.index).dt.to_period("M")
        mask[:] = (periods != periods.shift(1)).to_numpy()
    else:
        raise ValueError(f"unknown rebalance {rebalance}")
    # dam bao ky dau tien co position
    if not mask.any():
        mask.iloc[0] = True
    return pos.where(mask, np.nan).ffill().fillna(0.0)


def random_control(
    data: dict[str, pd.DataFrame],
    target_vol: float,
    rebalance: str = "monthly",
    sims: int = 300,
    seed: int = 7,
) -> np.ndarray:
    """Random long/short cung vol-target VA cung nhip rebalance: phan phoi Sharpe 'khong co edge'.

    Neu random doi dau moi ngay thi turnover vo ly -> Sharpe am gia tao, khong phai
    control cong bang. Do do random sign duoc giu co dinh trong moi ky rebalance,
    giong dung cach strategy that cap nhat position.
    """
    rng = np.random.default_rng(seed)
    sharpes = []
    for _ in range(sims):
        rets = {}
        for sym, df in data.items():
            close = df["close"]
            rv = realized_vol(close, 20)
            lev = (target_vol / rv.clip(lower=0.02)).clip(upper=3.0)
            sign = pd.Series(rng.choice([-1.0, 1.0], size=len(close)), index=close.index)
            pos = _rebalance_hold(sign * lev, rebalance)
            r = close.pct_change().fillna(0.0)
            held = pos.shift(1).fillna(0.0)
            to = held.diff().abs().fillna(held.abs())
            rets[sym] = held * r - to * (SYMBOL_COST_BPS.get(sym, 5.0) / 1e4) * 2.0
        port = (pd.DataFrame(rets).sort_index() * (1.0 / len(rets))).sum(axis=1, min_count=len(rets)).dropna()
        sd = port.std(ddof=1)
        sharpes.append(float(port.mean() / sd * math.sqrt(bars_per_year(port.index))) if sd > 0 else 0.0)
    return np.array(sharpes)


def cscv_pbo(perf: pd.DataFrame, s_blocks: int = 8) -> dict:
    """Probability of Backtest Overfitting (Bailey et al. 2015, CSCV).

    perf: DataFrame index=thoi gian, cot = tung cau hinh tham so.
    """
    n_obs = len(perf)
    if n_obs < s_blocks * 8:
        return {"pbo": None, "reason": "not enough observations"}
    idx = np.array_split(np.arange(n_obs), s_blocks)
    combos = list(itertools.combinations(range(s_blocks), s_blocks // 2))
    logits = []
    for combo in combos:
        is_rows = np.concatenate([idx[i] for i in combo])
        oos_rows = np.concatenate([idx[i] for i in range(s_blocks) if i not in combo])
        is_sh = perf.iloc[is_rows].mean() / perf.iloc[is_rows].std(ddof=1).replace(0, np.nan)
        oos_sh = perf.iloc[oos_rows].mean() / perf.iloc[oos_rows].std(ddof=1).replace(0, np.nan)
        best = is_sh.idxmax()
        if best not in oos_sh.index or pd.isna(oos_sh[best]):
            continue
        rank = oos_sh.rank(pct=True)[best]
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1 - rank)))
    if not logits:
        return {"pbo": None, "reason": "no valid splits"}
    logits_arr = np.array(logits)
    return {
        "pbo": round(float((logits_arr < 0).mean()), 3),
        "logit_mean": round(float(logits_arr.mean()), 3),
        "combos": len(logits),
    }


def deflated_sharpe(observed_sr: float, n_trials: int, n_obs: int, sr_std: float) -> dict:
    """DSR xap xi (Bailey & Lopez de Prado 2014)."""
    if n_trials < 1 or n_obs < 2 or sr_std <= 0:
        return {"dsr": None}
    euler = 0.5772156649
    expected_max = sr_std * (
        (1 - euler) * _norm_ppf(1 - 1.0 / n_trials) + euler * _norm_ppf(1 - 1.0 / (n_trials * math.e))
    )
    se = math.sqrt((1 + 0.5 * observed_sr**2) / max(n_obs - 1, 1))
    z = (observed_sr - expected_max) / se
    return {
        "dsr": round(float(_norm_cdf(z)), 4),
        "deflated_sr": round(float(observed_sr - expected_max), 3),
        "expected_max_sr": round(float(expected_max), 3),
        "n_trials": n_trials,
        "n_obs": n_obs,
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    # Acklam approximation, du chinh xac cho p in (0,1)
    a = [-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00]
    b = [-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def regime_table(ret: pd.Series) -> dict:
    """CAGR theo tung nam de thay regime dependency."""
    yearly = (1.0 + ret).groupby(ret.index.year).prod() - 1.0
    return {str(y): round(float(v) * 100, 2) for y, v in yearly.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(Path.home() / "fx-data" / "parquet"))
    ap.add_argument("--out", default=str(Path.home() / "fx-results"))
    ap.add_argument("--target-vol", type=float, default=0.05)
    ap.add_argument("--exclude", default="", help="bo symbol, vd: BTCUSD")
    ap.add_argument("--tag", default="", help="hau to ten file ket qua")
    args = ap.parse_args()

    data = load_d1(Path(args.data_dir))
    for sym in [s.strip() for s in args.exclude.split(",") if s.strip()]:
        data.pop(sym, None)
    if not data:
        raise SystemExit("no D1 parquet found")
    print("symbols:", {k: (len(v), str(v.index[0].date()), str(v.index[-1].date())) for k, v in data.items()})

    results: dict = {"symbols": {k: {"bars": len(v), "start": str(v.index[0].date()), "end": str(v.index[-1].date())} for k, v in data.items()}, "config": {"target_vol_per_symbol": args.target_vol, "cost_bps": SYMBOL_COST_BPS}}

    bh_ret, bh_to = buy_and_hold(data, args.target_vol)
    results["buy_hold"] = asdict(metrics("buy_hold_vol_targeted", bh_ret, bh_to))

    combos = [(21, "weekly"), (63, "weekly"), (126, "weekly"), (252, "weekly"),
              (21, "monthly"), (63, "monthly"), (126, "monthly"), (252, "monthly")]
    vol_grid = [0.03, 0.05, 0.08]
    trials = [("tsmom", lb, rb, tv) for lb, rb in combos for tv in vol_grid]
    trials += [("xsmom", lb, rb, tv) for lb, rb in combos for tv in vol_grid]

    perf_cols = {}
    rows = []
    for family, lookback, rebalance, tvol in trials:
        if family == "tsmom":
            ret, to, _ = portfolio_returns(data, lookback, tvol, rebalance)
        else:
            ret, to = xsmom_returns(data, lookback, rebalance, tvol)
        m = metrics(f"{family}_l{lookback}_{rebalance}_v{tvol}", ret, to)
        rows.append(asdict(m))
        perf_cols[m.name] = ret
        print(f"{m.name:34s} CAGR={m.cagr_pct:7.2f}% vol={m.vol_pct:6.2f}% SR={m.sharpe:6.2f} DD={m.max_dd_pct:7.2f}%")

    results["grid"] = rows

    # chon cau hinh "tot nhat" theo Sharpe in-sample de kiem tra overfitting
    perf = pd.DataFrame(perf_cols).dropna()
    best_row = max(rows, key=lambda r: r["sharpe"])
    best_name = best_row["name"]
    best_cfg = trials[rows.index(best_row)]
    results["best_in_sample"] = best_row
    results["best_config"] = {"family": best_cfg[0], "lookback": best_cfg[1], "rebalance": best_cfg[2], "target_vol": best_cfg[3]}
    results["best_regime_table"] = regime_table(perf_cols[best_name])

    best_ret = perf_cols[best_name]
    results["pbo"] = cscv_pbo(perf)

    # DSR phai dung CUNG don vi: SR theo bar (khong annualise) cho ca observed va trial spread
    sr_bar = float(best_ret.mean() / best_ret.std(ddof=1))
    sr_bar_std = float((perf.mean(axis=0) / perf.std(axis=0)).std(ddof=1))
    results["dsr"] = deflated_sharpe(sr_bar, n_trials=len(trials), n_obs=len(best_ret), sr_std=sr_bar_std)
    results["dsr"]["observed_sharpe_annual"] = round(sr_bar * math.sqrt(bars_per_year(best_ret.index)), 3)
    results["dsr"]["trial_sr_std_bar"] = round(sr_bar_std, 5)

    # per-symbol attribution chi co nghia voi tsmom (position doc lap tung symbol)
    per_best = {}
    if best_cfg[0] == "tsmom":
        _, best_lb, best_reb, best_tv = best_cfg
        for sym, df in data.items():
            ret_s, to_s = backtest_symbol(df, best_lb, best_tv, best_reb, SYMBOL_COST_BPS.get(sym, 5.0))
            per_best[sym] = asdict(metrics(f"{sym}_best_cfg", ret_s, to_s))
    results["per_symbol_best"] = per_best

    # benchmark tho: buy & hold khong leverage, khong vol target
    raw_bh = {}
    for sym, df in data.items():
        close = df["close"]
        r = close.pct_change().fillna(0.0)
        raw_bh[sym] = asdict(metrics(f"{sym}_raw_bh", r))

    results["raw_buy_hold"] = raw_bh

    ctrl = random_control(data, args.target_vol, rebalance=best_cfg[2])
    sr = sr_bar * math.sqrt(TRADING_DAYS)
    results["random_control"] = {
        "sims": int(len(ctrl)),
        "sharpe_mean": round(float(ctrl.mean()), 3),
        "sharpe_p50": round(float(np.percentile(ctrl, 50)), 3),
        "sharpe_p95": round(float(np.percentile(ctrl, 95)), 3),
        "sharpe_max": round(float(ctrl.max()), 3),
        "best_strategy_sharpe": round(sr, 3),
        "percentile_vs_random": round(float((ctrl < sr).mean()) * 100, 1),
    }

    # attribution: beta vao buy&hold gold
    if "XAUUSD" in data:
        gold = data["XAUUSD"]["close"].pct_change().reindex(best_ret.index).fillna(0.0)
        beta = float(np.polyfit(gold, best_ret, 1)[0])
        resid = best_ret - beta * gold
        results["gold_beta"] = {
            "beta": round(beta, 3),
            "alpha_annual_pct": round(float(resid.mean() * TRADING_DAYS) * 100, 2),
            "resid_sharpe": round(float(resid.mean() / resid.std(ddof=1) * math.sqrt(TRADING_DAYS)), 3),
        }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"p1_baseline_d1{args.tag}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("buy_hold", "best_in_sample", "pbo", "dsr", "random_control", "gold_beta") if k in results}, indent=2, ensure_ascii=False))
    print("\nwrote", out / f"p1_baseline_d1{args.tag}.json")


if __name__ == "__main__":
    main()
