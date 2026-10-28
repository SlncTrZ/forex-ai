"""Parameter Calibration & Grid Search for Supertrend + Volume and Pro Volatility Expansion V2.
Wing: code | Topic: parameter_calibration | Updated: 2026-10-28 13:35
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd

from forex_ai.research.models.dynamic_spread import DynamicSpreadModel
from forex_ai.research.models.slippage_impact import SlippageModel
from forex_ai.research.monte_carlo import permute_trade_sequences_with_ruin
from forex_ai.research.realistic_engine import RealisticEventEngine
from forex_ai.strategy.v1.contracts import Candle, MarketSnapshot, StrategyConfig, StrategyVersion, TimeframeSnapshot
from forex_ai.strategy.v2 import pro_volatility_expansion_v2, supertrend_volume_m5

UTC = timezone.utc


def load_m5_snapshots(parquet_path: Path, symbol: str, point: float, limit: int = 40000) -> list[MarketSnapshot]:
    df = pd.read_parquet(parquet_path)
    records = df.to_dict(orient="records")
    if limit and len(records) > limit:
        records = records[-limit:]  # Lấy N nến gần nhất

    candles = [
        Candle(
            datetime.fromtimestamp(int(r["time"]), UTC),
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            float(r.get("tick_volume") or r.get("real_volume") or 0.0),
        )
        for r in records
    ]

    snapshots: list[MarketSnapshot] = []
    # Build moving snapshots
    for i in range(50, len(candles)):
        bar = candles[i]
        clock = bar.time_utc
        history = candles[max(0, i - 100) : i + 1]
        spread = float(records[i].get("spread") or 20.0) * point
        bid = bar.close
        ask = bar.close + spread

        snap = MarketSnapshot(
            symbol=symbol,
            captured_at_utc=clock,
            market_time_msc=int(clock.timestamp() * 1000),
            bid=bid,
            ask=ask,
            timeframes={"M5": TimeframeSnapshot("M5", tuple(history))},
            spread_cost=spread,
        )
        snapshots.append(snap)

    return snapshots


def evaluate_param_set(
    strategy_fn: Any,
    strategy_id: str,
    params: dict[str, Any],
    snapshots: list[MarketSnapshot],
    point: float,
    initial_balance: float = 10_000.0,
) -> dict[str, Any]:
    cfg = StrategyConfig(StrategyVersion(strategy_id, "2.0.0"), params)
    engine = RealisticEventEngine(
        strategy=strategy_fn,
        config=cfg,
        initial_balance=initial_balance,
        leverage=100.0,
        spread_model=DynamicSpreadModel(),
        slippage_model=SlippageModel(seed=42),
    )

    for snap in snapshots:
        engine.process_snapshot(snap, snap.captured_at_utc)

    rep = engine.generate_report()
    trade_r = [t.net_pnl / 100.0 for t in rep.trades]
    mc = permute_trade_sequences_with_ruin(trade_r, ruin_threshold_r=10.0, iterations=500, seed=42)

    return {
        "params": params,
        "trades": rep.total_trades,
        "win_rate": round(rep.win_rate_pct, 1),
        "profit_factor": round(rep.profit_factor, 2),
        "net_pnl": round(rep.total_net_pnl, 1),
        "return_pct": round((rep.total_net_pnl / initial_balance) * 100.0, 1),
        "max_drawdown_pct": round(rep.max_drawdown_pct, 1),
        "mdd_95_r": round(mc.max_drawdown_r_p95, 1),
        "risk_of_ruin_pct": round(mc.risk_of_ruin_pct, 1),
        # Score cân bằng 'bát cơm' thực chiến: PnL / (Max Drawdown + 1) * WinRatePenalty
        "rice_bowl_score": round(
            (rep.total_net_pnl / max(rep.max_drawdown_amount, 100.0))
            * (min(rep.win_rate_pct, 60.0) / 50.0)
            * (1.0 if rep.total_trades >= 50 else 0.5),
            2,
        ),
    }


def calibrate_supertrend_m5(snapshots: list[MarketSnapshot], symbol: str, point: float) -> list[dict[str, Any]]:
    # Lưới tham số thực tế:
    # Period: 10, 14
    # Multiplier: 2.0, 2.5, 3.0, 3.5
    # Min Volume Ratio: 1.1, 1.3, 1.5
    # Target R: 1.5, 1.8, 2.0
    periods = [10, 14]
    multipliers = [2.0, 2.5, 3.0]
    vol_ratios = [1.1, 1.3, 1.5]
    targets = [1.5, 1.8, 2.0]

    results = []
    total_combos = len(periods) * len(multipliers) * len(vol_ratios) * len(targets)
    print(f"--- Đang hiệu chuẩn Supertrend M5 ({total_combos} tổ hợp)... ---")

    for p_val, m_val, v_val, t_val in itertools.product(periods, multipliers, vol_ratios, targets):
        params = {
            "supertrend_period": p_val,
            "supertrend_multiplier": m_val,
            "min_volume_ratio": v_val,
            "target_r": t_val,
            "allow_pullback_entry": True,
            "expiry_minutes": 60,
        }
        res = evaluate_param_set(supertrend_volume_m5.evaluate, "supertrend_volume_m5", params, snapshots, point)
        if res["trades"] >= 20:  # Đủ số lượng mẫu
            results.append(res)

    results.sort(key=lambda x: x["rice_bowl_score"], reverse=True)
    return results


def calibrate_pro_volatility_v2(snapshots: list[MarketSnapshot], symbol: str, point: float) -> list[dict[str, Any]]:
    body_ratios = [0.60, 0.70]
    range_atrs = [1.1, 1.3]
    vol_zs = [1.0, 1.3, 1.6]
    target_rs = [1.5, 2.0, 2.5]

    results = []
    for b_val, r_val, v_val, t_val in itertools.product(body_ratios, range_atrs, vol_zs, target_rs):
        params = {
            "min_body_ratio": b_val,
            "min_range_atr": r_val,
            "min_volume_zscore": v_val,
            "target_r": t_val,
            "atr_period": 14,
            "expiry_minutes": 90,
        }
        res = evaluate_param_set(pro_volatility_expansion_v2.evaluate, "pro_volatility_expansion_v2", params, snapshots, point)
        if res["trades"] >= 20:
            results.append(res)

    results.sort(key=lambda x: x["rice_bowl_score"], reverse=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Hiệu chuẩn tham số 'bát cơm' cho Supertrend M5 và Pro Volatility V2.")
    parser.add_argument("--symbol", default="XAUUSD", choices=["XAUUSD", "EURUSD"])
    parser.add_argument("--data-dir", default="backtest/data/historical_2y")
    parser.add_argument("--limit-bars", type=int, default=30000, help="Số nến M5 nạp vào để grid search (mặc định 30,000 ~ 5 tháng)")
    parser.add_argument("--output", default="backtest/data/calibration_results.json")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    m5_file = data_dir / f"{args.symbol}_M5.parquet"
    if not m5_file.exists():
        print(f"[ERROR] Không tìm thấy file: {m5_file}")
        return 1

    point = 0.001 if args.symbol == "XAUUSD" else 0.00001
    print(f"=== ĐANG NẠP {args.limit_bars:,} NẾN M5 CHO {args.symbol} ===")
    snapshots = load_m5_snapshots(m5_file, f"{args.symbol}m", point, limit=args.limit_bars)
    print(f"Đã nạp {len(snapshots):,} snapshots từ {snapshots[0].captured_at_utc.date()} đến {snapshots[-1].captured_at_utc.date()}!")

    # 1. Calibrate Supertrend M5
    st_results = calibrate_supertrend_m5(snapshots, args.symbol, point)
    print(f"\n=== KẾT QUẢ TOP 3 HIỆU CHUẨN SUPERTREND + VOLUME M5 ({args.symbol}) ===")
    for idx, r in enumerate(st_results[:3], 1):
        p = r["params"]
        print(f"Top {idx}: Period={p['supertrend_period']}, Mult={p['supertrend_multiplier']}, VolRatio={p['min_volume_ratio']}, TargetR={p['target_r']}")
        print(f"       -> Lệnh: {r['trades']} | Winrate: {r['win_rate']}% | PF: {r['profit_factor']} | Lãi: ${r['net_pnl']} (+{r['return_pct']}%) | MDD: {r['max_drawdown_pct']}% | RoR: {r['risk_of_ruin_pct']}% | Score: {r['rice_bowl_score']}")

    # 2. Calibrate Pro Volatility V2
    print(f"\n--- Đang hiệu chuẩn Pro Volatility Expansion V2... ---")
    v2_results = calibrate_pro_volatility_v2(snapshots, args.symbol, point)
    print(f"\n=== KẾT QUẢ TOP 3 HIỆU CHUẨN PRO VOLATILITY EXPANSION V2 ({args.symbol}) ===")
    for idx, r in enumerate(v2_results[:3], 1):
        p = r["params"]
        print(f"Top {idx}: BodyRatio={p['min_body_ratio']}, RangeATR={p['min_range_atr']}, VolZ={p['min_volume_zscore']}, TargetR={p['target_r']}")
        print(f"       -> Lệnh: {r['trades']} | Winrate: {r['win_rate']}% | PF: {r['profit_factor']} | Lãi: ${r['net_pnl']} (+{r['return_pct']}%) | MDD: {r['max_drawdown_pct']}% | RoR: {r['risk_of_ruin_pct']}% | Score: {r['rice_bowl_score']}")

    # Ghi file kết quả
    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "symbol": args.symbol,
        "bars_tested": len(snapshots),
        "supertrend_m5_top": st_results[:5],
        "pro_volatility_v2_top": v2_results[:5],
    }
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nĐã lưu kết quả hiệu chuẩn tại: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
