from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
from pathlib import Path

from forex_ai.intelligence.daily_auditor import (
    collect_daily_metrics,
    generate_market_outlook,
    render_markdown_daily_report,
)
from forex_ai.journal.db import initialize

UTC = timezone.utc


def test_collect_daily_metrics_and_cash_flow_separation(tmp_path):
    db_file = tmp_path / "test_forex.db"
    initialize(db_file)

    con = sqlite3.connect(db_file)
    target_date = date(2026, 10, 29)
    start_dt = datetime(2026, 10, 29, 0, 0, 0, tzinfo=UTC)
    t1 = start_dt + timedelta(hours=2)
    t2 = start_dt + timedelta(hours=4)
    t3 = start_dt + timedelta(hours=5)

    # 1. Accounts snapshots
    con.execute(
        "INSERT INTO accounts (timestamp, login, server, currency, balance, equity, margin, free_margin, margin_level, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (start_dt.isoformat(), 123, "Broker", "USD", 10000.0, 10000.0, 0.0, 10000.0, 1000.0, "{}")
    )
    con.execute(
        "INSERT INTO accounts (timestamp, login, server, currency, balance, equity, margin, free_margin, margin_level, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (t3.isoformat(), 123, "Broker", "USD", 1000.0, 1000.0, 0.0, 1000.0, 1000.0, "{}")
    )

    # 2. Deals: 1 trading win deal, 1 trading loss deal, 1 withdrawal balance deal
    con.execute(
        "INSERT INTO mt5_deals (ticket, order_ticket, time, time_msc, type, entry, magic, position_id, symbol, volume, price, commission, swap, profit, fee, reason, comment, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, 10, int(t1.timestamp()), int(t1.timestamp()*1000), 0, 1, 260904, 100, "XAUUSDm", 0.05, 4050.0, -0.5, 0.0, 25.0, 0.0, 5, "[tp 4050.0]", "{}")
    )
    con.execute(
        "INSERT INTO mt5_deals (ticket, order_ticket, time, time_msc, type, entry, magic, position_id, symbol, volume, price, commission, swap, profit, fee, reason, comment, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (2, 20, int(t2.timestamp()), int(t2.timestamp()*1000), 1, 1, 260904, 200, "XAUUSDm", 0.05, 4040.0, -0.5, 0.0, -15.0, 0.0, 4, "[sl 4040.0]", "{}")
    )
    # Withdrawal deal: type = 2 (DEAL_TYPE_BALANCE), profit = -9000.0
    con.execute(
        "INSERT INTO mt5_deals (ticket, order_ticket, time, time_msc, type, entry, magic, position_id, symbol, volume, price, commission, swap, profit, fee, reason, comment, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (3, 0, int(t3.timestamp()), int(t3.timestamp()*1000), 2, 0, 0, 0, "", 0.0, 0.0, 0.0, 0.0, -9000.0, 0.0, 0, "W-test-withdrawal", "{}")
    )
    con.commit()
    con.close()

    metrics = collect_daily_metrics(db_file, target_date)

    # Thẩm định phân tách dòng tiền và kết quả trading:
    assert metrics.net_cash_flow == Decimal("-9000.0")
    # Lãi ròng trading = (25.0 - 0.5) + (-15.0 - 0.5) = 24.5 - 15.5 = 9.0
    assert metrics.net_trading_pnl == Decimal("9.0")
    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate_pct == Decimal("50")
    assert metrics.profit_factor == Decimal("24.5") / Decimal("15.5")
    # Tài khoản sau rút về vốn $1000 -> Đạt Level 0 chuẩn xác!
    assert metrics.end_level == 0


def test_market_outlook_gotobi_and_friday():
    dummy_metrics = collect_daily_metrics.__annotations__  # check type
    from forex_ai.intelligence.daily_auditor import DailyMetrics
    m = DailyMetrics(
        audit_date="2026-10-29",
        start_equity=Decimal("1000"),
        end_equity=Decimal("1050"),
        start_level=0,
        end_level=0,
        risk_pct=Decimal("3.0"),
        net_trading_pnl=Decimal("50.0"),
        net_cash_flow=Decimal("0"),
        total_trades=4,
        winning_trades=3,
        losing_trades=1,
        win_rate_pct=Decimal("75"),
        profit_factor=Decimal("3.0"),
        max_drawdown_amount=Decimal("10.0"),
        max_drawdown_pct=Decimal("1.0"),
        total_friction=Decimal("2.0"),
        spread_cost=Decimal("1.0"),
        commission_cost=Decimal("1.0"),
        swap_cost=Decimal("0"),
        manual_closes_count=1,
        manual_pnl=Decimal("20.0"),
    )
    # Target date = 2026-10-29 (Thursday) -> next day is 2026-10-30 (Friday & Gotobi day 30!)
    outlook = generate_market_outlook(date(2026, 10, 29), m)
    assert outlook["next_date"] == "2026-10-30"
    assert outlook["weekday"] == "Friday"
    assert any("Gotobi" in ins for ins in outlook["insights"])
    assert any("Thứ Sáu" in ins for ins in outlook["insights"])

    report_md = render_markdown_daily_report(m, outlook)
    assert "# Báo Cáo Tổng Kết & Nghiệm Thu Giao Dịch Ngày 2026-10-29" in report_md
    assert "Level 0" in report_md
    assert "Gotobi" in report_md
