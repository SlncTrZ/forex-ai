"""Comprehensive 2-Year Realistic Backtest Runner — Multi-strategy execution with realistic frictions.
Wing: code | Topic: comprehensive_backtest | Updated: 2026-10-28 13:20
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd

from forex_ai.research.models.dynamic_spread import DynamicSpreadModel
from forex_ai.research.models.slippage_impact import SlippageModel
from forex_ai.research.monte_carlo import permute_trade_sequences_with_ruin
from forex_ai.research.mt5_dataset import build_replay_events_from_mt5_bars
from forex_ai.research.realistic_engine import RealisticEventEngine
from forex_ai.strategy.config import load_strategy_snapshot
from forex_ai.strategy.v1 import (
    breakout_retest,
    inside_bar_momentum_breakout,
    trend_pullback,
    volatility_breakout,
)

UTC = timezone.utc


STRATEGY_DISPATCH = {
    "trend_pullback_v1": trend_pullback.evaluate,
    "volatility_breakout_v1": volatility_breakout.evaluate,
    "inside_bar_momentum_breakout_v1": inside_bar_momentum_breakout.evaluate,
    "breakout_retest_v1": breakout_retest.evaluate,
}


def load_parquet_rows(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        raise FileNotFoundError(f"Missing parquet file: {file_path}")
    df = pd.read_parquet(file_path)
    return df.to_dict(orient="records")


def run_strategy_backtest(
    strategy_id: str,
    strategy_fn: Any,
    strategy_cfg: Any,
    events: list[Any],
    initial_balance: float = 10_000.0,
    point_value: float = 0.001,
) -> dict[str, Any]:
    engine = RealisticEventEngine(
        strategy=strategy_fn,
        config=strategy_cfg,
        initial_balance=initial_balance,
        leverage=100.0,
        spread_model=DynamicSpreadModel(),
        slippage_model=SlippageModel(seed=42),
    )

    for event in events:
        engine.process_snapshot(event.snapshot, event.clock_utc)

    rep = engine.generate_report()
    trade_r_values = []
    for t in rep.trades:
        # Net R = net_pnl / risk
        trade_r_values.append(t.net_pnl / 100.0)  # Chuẩn hóa theo risk 1% ($100)

    mc_ruin = permute_trade_sequences_with_ruin(trade_r_values, ruin_threshold_r=10.0, iterations=2000, seed=42)

    return {
        "strategy_id": strategy_id,
        "initial_balance": rep.initial_balance,
        "final_balance": round(rep.final_balance, 2),
        "total_net_pnl": round(rep.total_net_pnl, 2),
        "return_pct": round((rep.total_net_pnl / rep.initial_balance) * 100.0, 2),
        "total_trades": rep.total_trades,
        "winning_trades": rep.winning_trades,
        "losing_trades": rep.losing_trades,
        "win_rate_pct": round(rep.win_rate_pct, 2),
        "profit_factor": round(rep.profit_factor, 2),
        "max_drawdown_amount": round(rep.max_drawdown_amount, 2),
        "max_drawdown_pct": round(rep.max_drawdown_pct, 2),
        "friction_costs": {
            "total_spread_cost": round(rep.total_spread_cost, 2),
            "total_slippage_cost": round(rep.total_slippage_cost, 2),
            "total_commission_cost": round(rep.total_commission_cost, 2),
            "total_swap_cost": round(rep.total_swap_cost, 2),
            "total_friction": round(
                rep.total_spread_cost + rep.total_slippage_cost + rep.total_commission_cost + rep.total_swap_cost, 2
            ),
        },
        "monte_carlo_risk_of_ruin": {
            "iterations": mc_ruin.iterations,
            "risk_of_ruin_pct": round(mc_ruin.risk_of_ruin_pct, 2),
            "mdd_r_median": round(mc_ruin.max_drawdown_r_median, 2),
            "mdd_r_p95": round(mc_ruin.max_drawdown_r_p95, 2),
            "mdd_r_p99": round(mc_ruin.max_drawdown_r_p99, 2),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Chạy kiểm thử thực tế 2 năm toàn diện cho Forex-AI.")
    parser.add_argument("--data-dir", default="backtest/data/historical_2y", help="Thư mục dữ liệu Parquet")
    parser.add_argument("--output", default="backtest/data/comprehensive_2y_benchmark.json", help="File JSON kết quả")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "EURUSD"], help="Symbols cần test")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    out_file = Path(args.output).expanduser()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    strategy_snapshot = load_strategy_snapshot()
    benchmark_report: dict[str, Any] = {
        "schema": "forex-ai-comprehensive-2y-realistic-benchmark-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "symbols": {},
    }

    for base_sym in args.symbols:
        print(f"\n=======================================================")
        print(f"=== ĐANG CHẠY BACKTEST THỰC TẾ 2 NĂM CHO: {base_sym} ===")
        print(f"=======================================================")

        m15_file = data_dir / f"{base_sym}_M15.parquet"
        h1_file = data_dir / f"{base_sym}_H1.parquet"
        h4_file = data_dir / f"{base_sym}_H4.parquet"

        if not (m15_file.exists() and h1_file.exists() and h4_file.exists()):
            print(f"[SKIP] Thiếu dữ liệu M15/H1/H4 cho {base_sym}")
            continue

        m15_rows = load_parquet_rows(m15_file)
        h1_rows = load_parquet_rows(h1_file)
        h4_rows = load_parquet_rows(h4_file)

        point = 0.001 if base_sym == "XAUUSD" else 0.00001
        print(f"Đang dựng chuỗi sự kiện ReplayEvent từ {len(m15_rows):,} nến M15...")
        events = build_replay_events_from_mt5_bars(
            symbol=f"{base_sym}m",
            point=point,
            m15_rows=m15_rows,
            h1_rows=h1_rows,
            h4_rows=h4_rows,
            history_bars=100,
            min_bars=30,
        )
        print(f"Đã sinh {len(events):,} sự kiện thị trường Point-in-Time!")

        benchmark_report["symbols"][base_sym] = {
            "total_events": len(events),
            "date_range": {
                "start": events[0].clock_utc.strftime("%Y-%m-%d") if events else None,
                "end": events[-1].clock_utc.strftime("%Y-%m-%d") if events else None,
            },
            "strategies": {},
        }

        for strat_id, strat_fn in STRATEGY_DISPATCH.items():
            strat_cfg = strategy_snapshot.config_for(strat_id)
            print(f"\n>>> Đang đánh giá chiến lược: {strat_id}...")
            strat_res = run_strategy_backtest(
                strategy_id=strat_id,
                strategy_fn=strat_fn,
                strategy_cfg=strat_cfg,
                events=events,
                initial_balance=10_000.0,
                point_value=point,
            )
            benchmark_report["symbols"][base_sym]["strategies"][strat_id] = strat_res

            print(f"    - Số lệnh khớp: {strat_res['total_trades']}")
            print(f"    - Win Rate: {strat_res['win_rate_pct']}% ({strat_res['winning_trades']} thắng / {strat_res['losing_trades']} thua)")
            print(f"    - Profit Factor: {strat_res['profit_factor']}")
            print(f"    - Lợi nhuận ròng: ${strat_res['total_net_pnl']} ({strat_res['return_pct']}%)")
            print(f"    - Max Drawdown: {strat_res['max_drawdown_pct']}% (${strat_res['max_drawdown_amount']})")
            print(f"    - Tổng chi phí ma sát (Spread+Slip+Comm+Swap): ${strat_res['friction_costs']['total_friction']}")
            print(f"    - Monte Carlo Risk of Ruin: {strat_res['monte_carlo_risk_of_ruin']['risk_of_ruin_pct']}% (MDD 95%: {strat_res['monte_carlo_risk_of_ruin']['mdd_r_p95']}R)")

    out_file.write_text(json.dumps(benchmark_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[HOÀN TẤT] Báo cáo tổng thể đã lưu tại: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
