"""Monthly Backtest Runner — Segmented 1-Month Analysis with Tiered Compound & Max Active Orders = 3.
Wing: code | Topic: monthly_backtest | Updated: 2026-10-29 18:40
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from forex_ai.research.models.dynamic_spread import DynamicSpreadModel
from forex_ai.research.models.slippage_impact import SlippageModel
from forex_ai.research.monte_carlo import permute_trade_sequences_with_ruin
from forex_ai.research.mt5_dataset import build_replay_events_from_mt5_bars
from forex_ai.research.realistic_engine import RealisticEventEngine
from forex_ai.risk.tiered_compound import TieredCompoundRiskEngine
from forex_ai.strategy.config import load_strategy_snapshot
from forex_ai.strategy.v2 import (
    mtf_trend_continuation,
    smc_pro_fvg,
    supertrend_volume_m5,
)

UTC = timezone.utc

V2_PRODUCTION_STRATEGIES = {
    "supertrend_volume_m5": supertrend_volume_m5.evaluate,
    "smc_pro_fvg_v2": smc_pro_fvg.evaluate,
    "mtf_trend_continuation_v2": mtf_trend_continuation.evaluate,
}


def get_symbol_point(symbol: str) -> float:
    sym = symbol.upper()
    if "XAU" in sym or "GOLD" in sym:
        return 0.001
    elif "BTC" in sym:
        return 0.01
    elif "JPY" in sym:
        return 0.001
    else:
        return 0.00001


def load_parquet_rows(file_path: Path) -> list[dict[str, Any]]:
    if not file_path.exists():
        raise FileNotFoundError(f"Missing parquet file: {file_path}")
    df = pd.read_parquet(file_path)
    return df.to_dict(orient="records")


def backtest_symbol_month(
    base_sym: str,
    data_dir: Path,
    start_date: datetime,
    end_date: datetime,
    initial_balance: float = 10_000.0,
    max_active_orders: int = 3,
    use_tiered_sizing: bool = True,
) -> tuple[str, dict[str, Any] | None]:
    strategy_snapshot = load_strategy_snapshot()

    m5_file = data_dir / f"{base_sym}_M5.parquet"
    m15_file = data_dir / f"{base_sym}_M15.parquet"
    h1_file = data_dir / f"{base_sym}_H1.parquet"
    h4_file = data_dir / f"{base_sym}_H4.parquet"

    if not (m15_file.exists() and h1_file.exists() and h4_file.exists()):
        print(f"[{base_sym}] Thiếu dữ liệu M15/H1/H4")
        return base_sym, None

    m5_rows = load_parquet_rows(m5_file) if m5_file.exists() else None
    m15_rows = load_parquet_rows(m15_file)
    h1_rows = load_parquet_rows(h1_file)
    h4_rows = load_parquet_rows(h4_file)

    point = get_symbol_point(base_sym)
    all_events = build_replay_events_from_mt5_bars(
        symbol=f"{base_sym}m",
        point=point,
        m15_rows=m15_rows,
        h1_rows=h1_rows,
        h4_rows=h4_rows,
        m5_rows=m5_rows,
        history_bars=100,
        min_bars=30,
    )

    # Lọc sự kiện chính xác trong khung tháng mục tiêu (nhưng snapshot đã có đủ bars quá khứ để warm indicators)
    month_events = [e for e in all_events if start_date <= e.clock_utc <= end_date]
    if not month_events:
        print(f"[{base_sym}] Không có sự kiện trong khoảng {start_date.date()} -> {end_date.date()}")
        return base_sym, None

    print(f"[{base_sym}] Dựng {len(month_events):,} sự kiện trong tháng ({start_date.strftime('%Y-%m-%d')} đến {end_date.strftime('%Y-%m-%d')})...")

    sym_report: dict[str, Any] = {
        "symbol": base_sym,
        "total_events": len(month_events),
        "date_range": {
            "start": month_events[0].clock_utc.strftime("%Y-%m-%d %H:%M"),
            "end": month_events[-1].clock_utc.strftime("%Y-%m-%d %H:%M"),
        },
        "strategies": {},
    }

    tiered_engine = TieredCompoundRiskEngine(Decimal(str(initial_balance)))

    for strat_id, strat_fn in V2_PRODUCTION_STRATEGIES.items():
        strat_cfg = strategy_snapshot.config_for(strat_id)
        engine = RealisticEventEngine(
            strategy=strat_fn,
            config=strat_cfg,
            initial_balance=initial_balance,
            leverage=100.0,
            spread_model=DynamicSpreadModel(),
            slippage_model=SlippageModel(seed=42),
            use_tiered_sizing=use_tiered_sizing,
            max_active_orders=max_active_orders,
        )

        for event in month_events:
            engine.process_snapshot(event.snapshot, event.clock_utc)

        rep = engine.generate_report()
        trade_r_values = [t.net_pnl / (initial_balance * 0.03) for t in rep.trades] if rep.trades else []
        mc_ruin = permute_trade_sequences_with_ruin(trade_r_values, ruin_threshold_r=10.0, iterations=1000, seed=42) if trade_r_values else None

        final_lvl = tiered_engine.calculate_level(Decimal(str(max(0.0, rep.final_balance))))

        strat_res = {
            "strategy_id": strat_id,
            "initial_balance": rep.initial_balance,
            "final_balance": round(rep.final_balance, 2),
            "total_net_pnl": round(rep.total_net_pnl, 2),
            "return_pct": round((rep.total_net_pnl / rep.initial_balance) * 100.0, 2),
            "final_level": final_lvl,
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
                "risk_of_ruin_pct": round(mc_ruin.risk_of_ruin_pct, 2) if mc_ruin else 0.0,
                "mdd_r_median": round(mc_ruin.max_drawdown_r_median, 2) if mc_ruin else 0.0,
            },
        }
        sym_report["strategies"][strat_id] = strat_res
        print(f"  [{base_sym} - {strat_id}] Lệnh: {strat_res['total_trades']} | Win: {strat_res['win_rate_pct']}% | PF: {strat_res['profit_factor']} | PnL: ${strat_res['total_net_pnl']:+,.2f} ({strat_res['return_pct']}%) | Level: {final_lvl} | MDD: {strat_res['max_drawdown_pct']}%")

    return base_sym, sym_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Chạy kiểm thử backtest theo từng tháng độc lập.")
    parser.add_argument("--month", required=True, help="Tháng định dạng YYYY-MM (ví dụ: 2026-08, 2026-09, 2026-10)")
    parser.add_argument("--data-dir", default="backtest/data/historical_2y", help="Thư mục dữ liệu Parquet")
    parser.add_argument("--output", required=True, help="File JSON kết quả")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD", "EURUSD", "USDJPY"], help="Symbols cần test")
    parser.add_argument("--max-active-orders", type=int, default=3, help="Số lệnh mở tối đa đồng thời")
    parser.add_argument("--workers", type=int, default=4, help="Số tiến trình CPU")
    args = parser.parse_args()

    year_str, month_str = args.month.split("-")
    year = int(year_str)
    month = int(month_str)

    start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=UTC)
    else:
        end_date = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=UTC)

    data_dir = Path(args.data_dir).expanduser()
    out_file = Path(args.output).expanduser()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n================================================================================")
    print(f"=== BẮT ĐẦU BACKTEST THÁNG {args.month} ({start_date.strftime('%Y-%m-%d')} -> {end_date.strftime('%Y-%m-%d')}) ===")
    print(f"=== Cấu hình: Max Active Orders = {args.max_active_orders} | Tiered Compound = True ===")
    print(f"================================================================================\n")

    report: dict[str, Any] = {
        "schema": "forex-ai-monthly-benchmark-v1",
        "month": args.month,
        "max_active_orders": args.max_active_orders,
        "tiered_compound": True,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "symbols": {},
    }

    num_workers = min(args.workers, len(args.symbols))
    with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
        future_to_sym = {
            executor.submit(
                backtest_symbol_month,
                sym,
                data_dir,
                start_date,
                end_date,
                initial_balance=10_000.0,
                max_active_orders=args.max_active_orders,
                use_tiered_sizing=True,
            ): sym
            for sym in args.symbols
        }
        for future in concurrent.futures.as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                base_sym, sym_res = future.result()
                if sym_res:
                    report["symbols"][base_sym] = sym_res
            except Exception as e:
                print(f"[ERROR] Lỗi khi test {sym}: {e}")

    out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n[HOÀN TẤT] Báo cáo tháng {args.month} đã lưu tại: {out_file}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
