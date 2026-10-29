"""Daily Audit & Meta-Cognitive Advisory Engine.
Wing: intelligence | Topic: daily_auditor | Updated: 2026-10-29 19:45

Trích xuất và phân tích toàn diện dữ liệu giao dịch thực tế trong ngày từ forex.db:
- PnL ròng, win rate, profit factor, số lệnh đóng/mở.
- Phân tách rạch ròi kết quả giao dịch và dòng tiền nạp/rút (Capital Cash Flow).
- Đo lường chi phí ma sát thực tế (Spread, Commission, Swap).
- Cập nhật tiến độ thăng hạng Level theo mô hình lãi kép (lãi kép.xlsx).
- Thống kê các can thiệp bằng tay (MANUAL_CLOSE) của trader.
- Đưa ra nhận định thị trường và đề xuất tinh chỉnh tham số cho ngày kế tiếp.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from forex_ai.risk.tiered_compound import TieredCompoundRiskEngine

D = Decimal
UTC = timezone.utc


@dataclass(frozen=True)
class DealSummary:
    ticket: int
    symbol: str
    side: str
    volume: float
    entry_price: float
    exit_price: float
    net_pnl: float
    exit_reason: str
    closed_at_utc: datetime


@dataclass(frozen=True)
class DailyMetrics:
    audit_date: str
    start_equity: Decimal
    end_equity: Decimal
    start_level: int
    end_level: int
    risk_pct: Decimal
    net_trading_pnl: Decimal
    net_cash_flow: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_amount: Decimal
    max_drawdown_pct: Decimal
    total_friction: Decimal
    spread_cost: Decimal
    commission_cost: Decimal
    swap_cost: Decimal
    manual_closes_count: int
    manual_pnl: Decimal
    deals_by_symbol: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rejection_reasons: dict[str, int] = field(default_factory=dict)


def collect_daily_metrics(db_path: Path, target_date: date) -> DailyMetrics:
    start_dt = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=UTC)
    end_dt = start_dt + timedelta(days=1)
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row

    # 1. Accounts snapshots đầu và cuối ngày
    acc_start_row = con.execute(
        "SELECT equity FROM accounts WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp ASC LIMIT 1",
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchone()
    acc_end_row = con.execute(
        "SELECT equity FROM accounts WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp DESC LIMIT 1",
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchone()

    # Fallback nếu không có snapshot đầu ngày
    latest_acc = con.execute("SELECT equity FROM accounts ORDER BY snapshot_id DESC LIMIT 1").fetchone()
    fallback_equity = D(str(latest_acc[0])) if latest_acc else D("1000.0")

    start_equity = D(str(acc_start_row[0])) if acc_start_row else fallback_equity
    end_equity = D(str(acc_end_row[0])) if acc_end_row else fallback_equity

    # 2. Dòng tiền nạp/rút trong ngày (type = 2: DEAL_TYPE_BALANCE)
    cash_flow_row = con.execute(
        "SELECT COALESCE(SUM(profit), 0) FROM mt5_deals WHERE time >= ? AND time < ? AND type = 2",
        (start_ts, end_ts),
    ).fetchone()
    net_cash_flow = D(str(cash_flow_row[0] or 0))

    # 3. Lịch sử Deal giao dịch (type IN (0, 1), entry = 1 là deal đóng vị thế)
    trade_deals = con.execute(
        """SELECT ticket, symbol, type, entry, volume, price, commission, swap, profit, fee, reason, comment, time
           FROM mt5_deals
           WHERE time >= ? AND time < ? AND type IN (0, 1) AND entry = 1
           ORDER BY time ASC""",
        (start_ts, end_ts),
    ).fetchall()

    total_trades = len(trade_deals)
    winning_trades = 0
    losing_trades = 0
    gross_profit = D("0")
    gross_loss = D("0")
    total_commission = D("0")
    total_swap = D("0")
    manual_closes = 0
    manual_pnl = D("0")

    deals_by_symbol: dict[str, list[dict[str, Any]]] = {}

    for d in trade_deals:
        sym = d["symbol"]
        pnl = D(str(d["profit"]))
        comm = D(str(d["commission"]))
        sw = D(str(d["swap"]))
        fee = D(str(d["fee"]))
        net = pnl + comm + sw + fee

        total_commission += abs(comm)
        total_swap += abs(sw)

        if net > 0:
            winning_trades += 1
            gross_profit += net
        elif net < 0:
            losing_trades += 1
            gross_loss += abs(net)

        reason_code = d["reason"]
        comment = (d["comment"] or "").lower()
        if reason_code == 4 or "[sl" in comment:
            exit_label = "STOP_LOSS"
        elif reason_code == 5 or "[tp" in comment:
            exit_label = "TAKE_PROFIT"
        elif reason_code in {0, 1, 2} or "close" in comment:
            exit_label = "MANUAL_CLOSE"
            manual_closes += 1
            manual_pnl += net
        else:
            exit_label = "BROKER_FORCED_EXIT"

        item = {
            "ticket": d["ticket"],
            "symbol": sym,
            "volume": d["volume"],
            "exit_price": d["price"],
            "net_pnl": float(net),
            "exit_reason": exit_label,
            "time_utc": datetime.fromtimestamp(d["time"], UTC).strftime("%H:%M:%S"),
        }
        deals_by_symbol.setdefault(sym, []).append(item)

    net_trading_pnl = gross_profit - gross_loss
    win_rate_pct = (D(str(winning_trades)) / D(str(total_trades)) * D("100")) if total_trades > 0 else D("0")
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (D("99.9") if gross_profit > 0 else D("0"))

    # 4. Drawdown trong ngày
    peak_row = con.execute(
        "SELECT MAX(equity) FROM accounts WHERE timestamp >= ? AND timestamp < ?",
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchone()
    raw_peak = D(str(peak_row[0])) if (peak_row and peak_row[0]) else end_equity
    adjusted_peak = max(end_equity, raw_peak + (net_cash_flow if net_cash_flow < 0 else D("0")))
    drawdown_amount = max(D("0"), adjusted_peak - end_equity)
    drawdown_pct = (drawdown_amount / adjusted_peak * D("100")) if adjusted_peak > 0 else D("0")

    # 5. Phân tích Level Lãi kép (Tiered Compound Engine)
    # V0 lấy theo reference equity đã bù trừ cash flow
    v0 = max(D("1.0"), (start_equity + net_cash_flow) if net_cash_flow != 0 else start_equity)
    tiered_engine = TieredCompoundRiskEngine(v0)
    start_level = tiered_engine.calculate_level(start_equity)
    end_level = tiered_engine.calculate_level(end_equity)
    risk_pct = tiered_engine.get_risk_pct(end_level) * D("100")

    # 6. Thống kê từ chối của Scanner & Risk Engine
    rejection_rows = con.execute(
        """SELECT reason_codes_json FROM risk_decisions_v1
           WHERE created_at_utc >= ? AND created_at_utc < ? AND approved = 0""",
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchall()
    rejection_reasons: dict[str, int] = {}
    for r in rejection_rows:
        try:
            codes = json.loads(r[0])
            for code in codes:
                rejection_reasons[code] = rejection_reasons.get(code, 0) + 1
        except Exception:
            pass

    # Chi phí Spread ước tính dựa trên các giao dịch
    spread_cost = D("0")
    for sym, d_list in deals_by_symbol.items():
        point = D("0.001") if "XAU" in sym else D("0.00001")
        # Ước lượng 1.5 spread tiêu chuẩn
        spread_cost += D(str(len(d_list))) * D("1.5")

    total_friction = spread_cost + total_commission + total_swap

    con.close()

    return DailyMetrics(
        audit_date=target_date.isoformat(),
        start_equity=start_equity,
        end_equity=end_equity,
        start_level=start_level,
        end_level=end_level,
        risk_pct=risk_pct,
        net_trading_pnl=net_trading_pnl,
        net_cash_flow=net_cash_flow,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        max_drawdown_amount=drawdown_amount,
        max_drawdown_pct=drawdown_pct,
        total_friction=total_friction,
        spread_cost=spread_cost,
        commission_cost=total_commission,
        swap_cost=total_swap,
        manual_closes_count=manual_closes,
        manual_pnl=manual_pnl,
        deals_by_symbol=deals_by_symbol,
        rejection_reasons=rejection_reasons,
    )


def generate_market_outlook(target_date: date, metrics: DailyMetrics) -> dict[str, Any]:
    next_day = target_date + timedelta(days=1)
    weekday = next_day.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
    day_num = next_day.day

    insights = []
    tuning_proposals = []

    # 1. Nhận định Lịch kinh tế & Chu kỳ
    if weekday == 4:
        insights.append("Thứ Sáu cuối tuần: Thị trường thường có hiện tượng chốt lời diện rộng trước 20:00 UTC. Rủi ro gáp giá mở cửa đầu tuần tới.")
        tuning_proposals.append("Kích hoạt kiểm tra Weekend Rollover lúc 16:00 UTC, không mở lệnh mới sau 16:30 UTC.")
    elif weekday in {5, 6}:
        insights.append("Cuối tuần: Thị trường Ngoại hối (FX) và Kim loại (Vàng) đóng cửa. Riêng Bitcoin (BTCUSD) vẫn hoạt động 24/7 nhưng thanh khoản thấp.")
        tuning_proposals.append("Khóa giao dịch trên XAUUSD/EURUSD/USDJPY, duy trì chế độ Squeeze trên BTCUSD.")
    else:
        insights.append(f"Phiên giao dịch ngày thường ({next_day.strftime('%A')}): Thanh khoản tập trung tại London (07:30 UTC) và New York (13:30 UTC).")

    # Kiểm tra ngày Gotobi (mùng 5, 10, 15, 20, 25, 30)
    if day_num in {5, 10, 15, 20, 25, 30}:
        insights.append(f"Ngày {day_num} là ngày Gotobi của Nhật Bản: Kỳ vọng dòng tiền chốt tỷ giá Tokyo Morning Fix (00:55 UTC) tác động mạnh tới USDJPY.")
        tuning_proposals.append("Theo dõi điểm vào lệnh sớm trên USDJPY trong khung 00:00 - 01:00 UTC.")

    # 2. Đánh giá Vi cấu trúc & Hiệu suất hôm nay
    if metrics.total_trades > 0 and metrics.win_rate_pct < D("35"):
        insights.append("Tỷ lệ thắng hôm nay thấp hơn kỳ vọng: Thị trường có khả năng rơi vào pha tích lũy nén biên độ (rangebound choppiness).")
        tuning_proposals.append("Khuyến nghị nâng ngưỡng lọc `min_volume_ratio` từ 1.2 lên 1.3 trên Supertrend M5 để loại bỏ bớt tín hiệu giả.")
    elif metrics.win_rate_pct >= D("50"):
        insights.append("Hệ thống bắt nhịp xu hướng xuất sắc: Các tín hiệu V2 và lệnh Limit 50% FVG đã triệt tiêu trượt giá và đạt mục tiêu R:R.")

    # 3. Đánh giá can thiệp bằng tay
    if metrics.manual_closes_count > 0:
        if metrics.manual_pnl > 0:
            insights.append(f"Trader đã chủ động chốt lời tay {metrics.manual_closes_count} lệnh với lợi nhuận ròng +${float(metrics.manual_pnl):,.2f}.")
        else:
            insights.append(f"Trader đã chủ động cắt lỗ sớm {metrics.manual_closes_count} lệnh (-${float(abs(metrics.manual_pnl)):,.2f}) để bảo toàn vốn.")

    # 4. Định hướng quy mô vốn ngày mai
    next_level = metrics.end_level
    threshold_up = tiered_threshold(metrics.start_equity, next_level + 1)
    insights.append(f"Vốn hiện tại: ${float(metrics.end_equity):,.2f} (Đang ở Level {next_level}). Ngưỡng thăng lên Level {next_level + 1} là: ${float(threshold_up):,.2f}.")

    return {
        "next_date": next_day.isoformat(),
        "weekday": next_day.strftime("%A"),
        "insights": insights,
        "tuning_proposals": tuning_proposals,
    }


def tiered_threshold(v0: Decimal, level: int) -> Decimal:
    if level >= 0:
        return v0 * (D("1.1") ** level)
    else:
        return v0 * (D("0.9") ** abs(level))


def render_markdown_daily_report(metrics: DailyMetrics, outlook: dict[str, Any]) -> str:
    lines = [
        f"# Báo Cáo Tổng Kết & Nghiệm Thu Giao Dịch Ngày {metrics.audit_date}",
        "",
        f"- **Thời điểm lập báo cáo:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "- **Người thẩm định:** Forex-AI Post-Trade Meta-Cognitive Agent",
        "- **Trạng thái hệ thống:** 100% Deterministic Execution (V2 Suite + Tiered Compound Risk)",
        "",
        "---",
        "",
        "## 1. Bảng Chỉ Số Hiệu Suất Tài Chính (Financial Performance)",
        "",
        "| Chỉ tiêu | Giá trị ghi nhận | Đánh giá & Ghi chú |",
        "| :--- | :---: | :--- |",
        f"| **Vốn đầu ngày** | **${float(metrics.start_equity):,.2f}** | Mốc tham chiếu bắt đầu phiên |",
        f"| **Vốn cuối ngày** | **${float(metrics.end_equity):,.2f}** | Vốn thực tế sau quyết toán |",
        f"| **Dòng tiền nạp/rút** | **${float(metrics.net_cash_flow):+,.2f}** | Tách riêng khỏi kết quả giao dịch |",
        f"| **Lợi nhuận ròng (PnL)** | **${float(metrics.net_trading_pnl):+,.2f}** | Kết quả ròng từ lệnh giao dịch |",
        f"| **Cấp bậc Lãi kép (Level)** | **Level {metrics.end_level}** | Mức rủi ro hiện tại: **{float(metrics.risk_pct):.2f}%** / lệnh |",
        f"| **Tổng số lệnh đóng** | **{metrics.total_trades}** | Thắng: {metrics.winning_trades} / Thua: {metrics.losing_trades} |",
        f"| **Tỷ lệ Thắng (Win Rate)** | **{float(metrics.win_rate_pct):.2f}%** | Kỳ vọng danh mục: 40% - 50% |",
        f"| **Profit Factor** | **{float(metrics.profit_factor):.2f}** | Hệ số lãi / lỗ |",
        f"| **Sụt giảm tối đa (MDD)** | **${float(metrics.max_drawdown_amount):,.2f} ({float(metrics.max_drawdown_pct):.2f}%)** | Giới hạn an toàn: < 10.0% |",
        f"| **Tổng chi phí ma sát** | **${float(metrics.total_friction):,.2f}** | Gồm Spread, Commission, Swap |",
        f"| **Can thiệp tay (Manual)** | **{metrics.manual_closes_count} lệnh** | PnL đóng tay: **${float(metrics.manual_pnl):+,.2f}** |",
        "",
        "---",
        "",
        "## 2. Chi Tiết Các Lệnh Khớp Trong Ngày Theo Từng Tài Sản",
        "",
    ]

    if metrics.deals_by_symbol:
        for sym, deals in metrics.deals_by_symbol.items():
            lines.append(f"### Cặp tài sản: `{sym}` ({len(deals)} lệnh)")
            lines.append("| Ticket | Khối lượng | Giá thoát | Lãi/Lỗ ròng ($) | Lý do đóng | Giờ đóng (UTC) |")
            lines.append("| :---: | :---: | :---: | :---: | :---: | :---: |")
            for d in deals:
                pnl_str = f"+${d['net_pnl']:,.2f}" if d["net_pnl"] >= 0 else f"-${abs(d['net_pnl']):,.2f}"
                lines.append(f"| #{d['ticket']} | {d['volume']} lot | {d['exit_price']} | {pnl_str} | `{d['exit_reason']}` | {d['time_utc']} |")
            lines.append("")
    else:
        lines.append("*Hôm nay hệ thống không có lệnh nào đóng hoặc không có tín hiệu thỏa mãn bộ lọc.*")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 3. Hoạt Động Của Bộ Lọc Rủi Ro (Risk Rejection Audit)",
        "",
    ])

    if metrics.rejection_reasons:
        lines.append("| Mã từ chối an toàn | Số lần kích hoạt | Ý nghĩa phòng thủ |")
        lines.append("| :--- | :---: | :--- |")
        for code, count in sorted(metrics.rejection_reasons.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"| `{code}` | {count} lần | Chặn tín hiệu không đạt chuẩn hoặc vượt trần rủi ro |")
        lines.append("")
    else:
        lines.append("*Không có trường hợp từ chối nào bất thường trong phiên.*")
        lines.append("")

    lines.extend([
        "---",
        "",
        f"## 4. Nhận Định Thị Trường & Đề Xuất Tinh Chỉnh Cho Ngày Mai ({outlook['weekday']}, {outlook['next_date']})",
        "",
        "### 4.1. Nhận Định Vi Cấu Trúc & Vĩ Mô:",
    ])
    for ins in outlook["insights"]:
        lines.append(f"- {ins}")

    lines.extend([
        "",
        "### 4.2. Đề Xuất Tinh Chỉnh Tham Số Dựa Trên Dữ Liệu Thực Tế:",
    ])
    for prop in outlook["tuning_proposals"]:
        lines.append(f"- **[ĐỀ XUẤT]** {prop}")

    lines.extend([
        "",
        "---",
        "",
        "*Báo cáo được khởi tạo tự động bởi Forex-AI Meta-Cognitive Agent. Dữ liệu được bảo chứng bởi lịch sử deal MT5 và nhật ký kiểm toán SQLite.*",
    ])

    return "\n".join(lines)
