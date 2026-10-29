#!/usr/bin/env python3
"""Run Daily Agent Audit — Rà soát kết quả giao dịch ngày & Đưa ra nhận định ngày kế tiếp.
Wing: intelligence | Topic: daily_agent_audit | Updated: 2026-10-29 19:50
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.intelligence.daily_auditor import (
    collect_daily_metrics,
    generate_market_outlook,
    render_markdown_daily_report,
)

UTC = timezone.utc


def main() -> int:
    parser = argparse.ArgumentParser(description="Chạy tổng hợp kết quả giao dịch ngày và đưa ra nhận định.")
    parser.add_argument("--date", help="Ngày cần thẩm định YYYY-MM-DD (mặc định: hôm nay theo UTC)")
    parser.add_argument("--db-path", help="Đường dẫn file SQLite forex.db")
    parser.add_argument("--output-dir", default="_private/research/daily-reports", help="Thư mục lưu báo cáo Markdown")
    args = parser.parse_args()

    cfg = load_runtime_config()
    db_file = Path(args.db_path).expanduser() if args.db_path else cfg.db_path

    if not db_file.exists():
        print(f"[ERROR] Không tìm thấy cơ sở dữ liệu tại: {db_file}", file=sys.stderr)
        return 1

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"[ERROR] Định dạng ngày không hợp lệ: {args.date} (Yêu cầu: YYYY-MM-DD)", file=sys.stderr)
            return 2
    else:
        target_date = datetime.now(UTC).date()

    print(f"=== ĐANG THỰC THI RÀ SOÁT GIAO DỊCH NGÀY {target_date.isoformat()} ===")
    print(f"Đọc dữ liệu từ: {db_file}")

    metrics = collect_daily_metrics(db_file, target_date)
    outlook = generate_market_outlook(target_date, metrics)
    md_content = render_markdown_daily_report(metrics, outlook)

    out_dir = Path(args.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{target_date.isoformat()}-daily-audit.md"
    out_file.write_text(md_content, encoding="utf-8")

    print("\n" + "=" * 60)
    print(f"=== TỔNG KẾT GIAO DỊCH NGÀY {metrics.audit_date} ===")
    print("=" * 60)
    print(f"• Vốn cuối ngày: ${float(metrics.end_equity):,.2f} | Dòng tiền nạp/rút: ${float(metrics.net_cash_flow):+,.2f}")
    print(f"• PnL ròng giao dịch: ${float(metrics.net_trading_pnl):+,.2f}")
    print(f"• Level Lãi kép: Level {metrics.end_level} (Rủi ro: {float(metrics.risk_pct):.2f}%/lệnh)")
    print(f"• Số lệnh đóng: {metrics.total_trades} (Thắng: {metrics.winning_trades} / Thua: {metrics.losing_trades}) | Win Rate: {float(metrics.win_rate_pct):.2f}%")
    print(f"• Profit Factor: {float(metrics.profit_factor):.2f} | Max Drawdown: ${float(metrics.max_drawdown_amount):,.2f} ({float(metrics.max_drawdown_pct):.2f}%)")
    print(f"• Lệnh đóng tay (Manual Close): {metrics.manual_closes_count} lệnh (PnL: ${float(metrics.manual_pnl):+,.2f})")
    print("-" * 60)
    print(f"=== NHẬN ĐỊNH CHO NGÀY MAI ({outlook['weekday']}, {outlook['next_date']}) ===")
    for ins in outlook["insights"]:
        print(f"  - {ins}")
    if outlook["tuning_proposals"]:
        print("=== ĐỀ XUẤT TINH CHỈNH THAM SỐ ===")
        for prop in outlook["tuning_proposals"]:
            print(f"  * {prop}")
    print("=" * 60)
    print(f"\n[HOÀN TẤT] Báo cáo chi tiết đã lưu tại: {out_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
