"""P2 — Cross-sectional tren universe rong: currency-factor momentum vs pair momentum.

Gia thuyet P2: trend that su khong nam o tung cap tien, ma o VI TRI TUONG DOI
cua 8 dong tien. P1 that bai vi chi co 3-4 symbol -> khong du cross-section.
P2 mo rong len ~28 cap + kim loai va xep hang theo suc manh tung dong tien.

Chay:
    python research/p2_xsection.py --data-dir ~/fx-data/parquet --out ~/fx-results
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from baseline_d1 import (  # noqa: E402
    SYMBOL_COST_BPS,
    TRADING_DAYS,
    _rebalance_hold,
    bars_per_year,
    cscv_pbo,
    deflated_sharpe,
    metrics,
    realized_vol,
    regime_table,
)

CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD")


def load_all(data_dir: Path, min_bars: int = 1500) -> dict[str, pd.DataFrame]:
    """Doc toan bo D1 trong data lake, tra ve dict symbol -> OHLC."""
    out: dict[str, pd.DataFrame] = {}
    for path in sorted(data_dir.glob("*_D1.parquet")):
        raw = path.name.split("_")[0]
        sym = raw[:-1] if raw.endswith("m") else raw
        if len(sym) != 6:
            continue
        df = pd.read_parquet(path)
        df["dt"] = pd.to_datetime(df["dt"], utc=True).dt.tz_localize(None)
        df = df.set_index("dt").sort_index()
        df = df[["open", "high", "low", "close"]].astype(float)
        df = df[~df.index.duplicated(keep="last")].dropna()
        if len(df) < min_bars:
            continue
        out[sym] = df
    return out


def currency_scores(mom: pd.DataFrame) -> pd.DataFrame:
    """Suc manh tung dong tien = trung binh log-return so voi moi doi thu.

    mom: DataFrame close/close.shift(L)-1, cot la cap tien 6 ky tu.
    Tra ve DataFrame index=thoi gian, cot la 8 dong tien.
    """
    cols = list(mom.columns)
    base = {c: c[:3] for c in cols}
    quote = {c: c[3:] for c in cols}
    scores = {}
    for cur in CURRENCIES:
        as_base = [c for c in cols if base[c] == cur]
        as_quote = [c for c in cols if quote[c] == cur]
        parts = []
        if as_base:
            parts.append(mom[as_base].mean(axis=1, skipna=True))
        if as_quote:
            parts.append(-mom[as_quote].mean(axis=1, skipna=True))
        if not parts:
            continue
        scores[cur] = pd.concat(parts, axis=1).mean(axis=1, skipna=True)
    return pd.DataFrame(scores)


def currency_factor_weights(
    closes: pd.DataFrame,
    lookback: int,
    target_vol: float,
    top_n: int = 3,
) -> pd.DataFrame:
    """Weight tung cap: long dong manh nhat, short dong yeu nhat (dollar-neutral).

    w(cap) = (score[base] - score[quote]) / so cap, sau do scale theo vol tung cap.
    """
    mom = closes / closes.shift(lookback) - 1.0
    scores = currency_scores(mom)
    if scores.empty:
        return pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    scores = scores.sort_index()

    # chuan hoa score theo cross-section tai tung thoi diem (z-score)
    z = scores.sub(scores.mean(axis=1), axis=0).div(scores.std(axis=1, ddof=1), axis=0)

    cols = list(closes.columns)
    w = pd.DataFrame(0.0, index=closes.index, columns=cols)
    for c in cols:
        b, q = c[:3], c[3:]
        if b in z.columns and q in z.columns:
            w[c] = z[b] - z[q]

    # chi giu top_n dong manh nhat va top_n dong yeu nhat -> giam nhieu
    def _sparsify(row: pd.Series) -> pd.Series:
        valid = row.dropna()
        if valid.empty:
            return row
        ranked = valid.sort_values()
        keep = pd.concat([ranked.head(top_n), ranked.tail(top_n)])
        mask = row.index.isin(keep.index)
        return row.where(mask, 0.0)

    w = w.apply(_sparsify, axis=1)

    vol = pd.DataFrame({s: realized_vol(closes[s], 20) for s in cols}).reindex(closes.index)
    lev = ((target_vol / math.sqrt(max(len(cols), 1))) / vol.clip(lower=0.02)).clip(upper=3.0)
    w = (w * lev).fillna(0.0)
    return w.sort_index()


def pair_xsmom_weights(closes: pd.DataFrame, lookback: int, target_vol: float, top_n: int = 5) -> pd.DataFrame:
    """Xep hang truc tiep theo momentum tung cap, long top / short bottom."""
    mom = closes / closes.shift(lookback) - 1.0
    ranks = mom.rank(axis=1, method="average", pct=True)
    n = ranks.notna().sum(axis=1)
    raw = (ranks - 0.5) * 2.0
    raw = raw.where(n >= 10, np.nan)

    def _sparsify(row: pd.Series) -> pd.Series:
        valid = row.dropna()
        if valid.empty:
            return row
        ranked = valid.sort_values()
        keep = pd.concat([ranked.head(top_n), ranked.tail(top_n)])
        return row.where(row.index.isin(keep.index), 0.0)

    raw = raw.apply(_sparsify, axis=1)
    vol = pd.DataFrame({s: realized_vol(closes[s], 20) for s in closes.columns}).reindex(closes.index)
    lev = ((target_vol / math.sqrt(max(closes.shape[1], 1))) / vol.clip(lower=0.02)).clip(upper=3.0)
    w = (raw * lev).fillna(0.0)
    return w.sort_index()


def simulate(
    weights: pd.DataFrame,
    closes: pd.DataFrame,
    rebalance: str,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bien weight thanh chuoi return net, khong look-ahead."""
    w = weights.apply(lambda col: _rebalance_hold(col, rebalance))
    held = w.shift(1).fillna(0.0)
    rets = closes.pct_change().fillna(0.0)
    turnover = held.diff().abs().fillna(held.abs())
    cost_rate = pd.Series({s: SYMBOL_COST_BPS.get(s, 3.0) / 1e4 for s in held.columns})
    cost = (turnover * cost_rate).sum(axis=1) * 2.0
    gross_expo = held.abs().sum(axis=1)
    port = (held * rets).sum(axis=1) - cost
    valid = gross_expo > 0
    return port[valid], turnover.sum(axis=1)[valid], gross_expo


def dollar_neutral_benchmark(closes: pd.DataFrame, target_vol: float, rebalance: str = "monthly") -> tuple[pd.Series, pd.Series, pd.Series]:
    """Long-only vol-target tren toan universe: benchmark 'khong co signal'."""
    vol = pd.DataFrame({s: realized_vol(closes[s], 20) for s in closes.columns}).reindex(closes.index)
    lev = ((target_vol / math.sqrt(max(closes.shape[1], 1))) / vol.clip(lower=0.02)).clip(upper=3.0)
    w = lev.where(closes.notna(), 0.0)
    return simulate(w, closes, rebalance)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(Path.home() / "fx-data" / "parquet"))
    ap.add_argument("--out", default=str(Path.home() / "fx-results"))
    ap.add_argument("--tag", default="_p2")
    ap.add_argument("--target-vol", type=float, default=0.08)
    args = ap.parse_args()

    data = load_all(Path(args.data_dir))
    if len(data) < 10:
        raise SystemExit(f"can >=10 symbol, chi co {len(data)}")

    closes = pd.DataFrame({s: df["close"] for s, df in data.items()}).sort_index()
    closes = closes[~closes.index.duplicated(keep="last")]
    # ffill BAT BUOC: cac symbol co lich khac nhau (BTC co thu 7, FX co chu nhat).
    # Khong ffill -> moi cot co NaN xen ke -> rolling vol 20 luon NaN -> weight = 0
    # am tham (da tung lam hong toan bo ket qua cross-section).
    closes = closes.ffill()
    # chi giu giai doan ma >= 20 symbol cung co du lieu
    coverage = closes.notna().sum(axis=1)
    start = coverage[coverage >= min(20, closes.shape[1])].index.min()
    closes = closes.loc[start:]
    print(f"universe={closes.shape[1]} symbols, chung minh tu {start:%Y-%m-%d} -> {closes.index[-1]:%Y-%m-%d}")

    results: dict = {
        "universe": sorted(closes.columns),
        "start": str(start.date()),
        "end": str(closes.index[-1].date()),
        "bars": int(len(closes)),
        "config": {"target_vol": args.target_vol},
    }

    trials = []
    for family in ("cf", "xsmom"):
        for lookback in (21, 63, 126, 252):
            for rebalance in ("weekly", "monthly"):
                trials.append((family, lookback, rebalance))

    perf_cols: dict[str, pd.Series] = {}
    rows = []
    for family, lookback, rebalance in trials:
        if family == "cf":
            w = currency_factor_weights(closes, lookback, args.target_vol)
        else:
            w = pair_xsmom_weights(closes, lookback, args.target_vol)
        ret, to, expo = simulate(w, closes, rebalance)
        if ret.empty:
            continue
        name = f"{family}_l{lookback}_{rebalance}"
        m = metrics(name, ret, to)
        row = asdict(m)
        row["coverage_pct"] = round(float((expo > 0).mean()) * 100, 1)
        row["median_gross"] = round(float(expo[expo > 0].median()), 2)
        rows.append(row)
        perf_cols[name] = ret
        print(
            f"{name:22s} CAGR={m.cagr_pct:7.2f}% vol={m.vol_pct:6.2f}% SR={m.sharpe:6.3f} "
            f"DD={m.max_dd_pct:7.2f}% turn={m.turnover_per_year:6.1f} cov={row['coverage_pct']:5.1f}%"
        )

    if not rows:
        raise SystemExit("khong co trial nao chay duoc")

    results["grid"] = rows
    best_row = max(rows, key=lambda r: r["sharpe"])
    results["best_in_sample"] = best_row
    results["best_regime_table"] = regime_table(perf_cols[best_row["name"]])

    # benchmark: long-only vol-target tren toan universe (khong signal)
    bh_ret, bh_to, _ = dollar_neutral_benchmark(closes, args.target_vol, "monthly")
    results["buy_hold"] = asdict(metrics("buy_hold_long_basket", bh_ret, bh_to))

    perf = pd.DataFrame(perf_cols).dropna()
    results["pbo"] = cscv_pbo(perf)
    best_ret = perf_cols[best_row["name"]]
    sr_bar = float(best_ret.mean() / best_ret.std(ddof=1))
    sr_bar_std = float((perf.mean(axis=0) / perf.std(axis=0)).std(ddof=1))
    results["dsr"] = deflated_sharpe(sr_bar, n_trials=len(trials), n_obs=len(best_ret), sr_std=sr_bar_std)
    results["dsr"]["observed_sharpe_annual"] = round(sr_bar * math.sqrt(bars_per_year(best_ret.index)), 3)

    # walk-forward THAT: chot tham so chi tren 70% dau, do tren 30% cuoi
    split = int(len(perf) * 0.7)
    is_perf = perf.iloc[:split]
    oos_perf = perf.iloc[split:]
    is_sh = is_perf.mean() / is_perf.std(ddof=1)
    wf_best = str(is_sh.idxmax())
    oos_ret = oos_perf[wf_best]
    is_ret = is_perf[wf_best]
    oos_bpy = bars_per_year(oos_ret.index)
    is_bpy = bars_per_year(is_ret.index)
    results["walk_forward"] = {
        "selected_on_is": wf_best,
        "is_sharpe": round(float(is_sh[wf_best] * math.sqrt(is_bpy)), 3),
        "oos_sharpe": round(float(oos_ret.mean() / oos_ret.std(ddof=1) * math.sqrt(oos_bpy)), 3),
        "is_cagr_pct": round(float((1 + is_ret).prod() ** (365.25 / (is_ret.index[-1] - is_ret.index[0]).days) - 1) * 100, 2),
        "oos_cagr_pct": round(float((1 + oos_ret).prod() ** (365.25 / (oos_ret.index[-1] - oos_ret.index[0]).days) - 1) * 100, 2),
        "oos_max_dd_pct": round(float(((1 + oos_ret).cumprod() / (1 + oos_ret).cumprod().cummax() - 1).min()) * 100, 2),
        "split_at": str(oos_ret.index[0].date()),
        "oos_days": int(len(oos_ret)),
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"p2_xsection{args.tag}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n=== BUY & HOLD ===")
    print(" ", results["buy_hold"])
    print("=== BEST ===")
    print(" ", best_row)
    print("=== PBO/DSR ===")
    print(" ", results["pbo"], results["dsr"])
    print("=== WALK FORWARD ===")
    print(" ", results["walk_forward"])
    print("\nwrote", out / f"p2_xsection{args.tag}.json")


if __name__ == "__main__":
    main()
