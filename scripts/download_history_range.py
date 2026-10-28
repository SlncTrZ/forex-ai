"""Historical Data Acquisition — 2-year multi-timeframe bar downloader for backtesting.
Wing: code | Topic: historical_download | Updated: 2026-10-28 12:45
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List

from forex_ai.config import load_runtime_config
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol
from forex_ai.strategy.v1.contracts import fingerprint

UTC = timezone.utc


def fetch_symbol_timeframe_history(
    client: MT5Client,
    actual_symbol: str,
    tf_int: int,
    start_utc: datetime,
    end_utc: datetime,
    chunk_days: int = 30,
) -> list[dict[str, Any]]:
    """Tải dữ liệu chia nhỏ theo từng chunk (mặc định 30 ngày) để tránh nghẽn timeout hoặc vượt giới hạn broker."""
    all_rows: list[dict[str, Any]] = []
    seen_times: set[int] = set()

    current_start = start_utc
    while current_start < end_utc:
        current_end = min(current_start + timedelta(days=chunk_days), end_utc)
        start_ts = current_start.timestamp()
        end_ts = current_end.timestamp()

        try:
            chunk = client.bars_range(actual_symbol, tf_int, start_ts, end_ts)
            for row in chunk:
                t = int(row["time"])
                if t not in seen_times:
                    seen_times.add(t)
                    all_rows.append(row)
        except Exception as exc:
            print(f"  [WARN] Failed chunk {current_start.date()} to {current_end.date()}: {exc}")

        current_start = current_end

    all_rows.sort(key=lambda r: int(r["time"]))
    return all_rows


def save_dataset(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pandas as pd
        df = pd.DataFrame(rows)
        if out_path.suffix == ".parquet":
            df.to_parquet(out_path, index=False)
            return
    except ImportError:
        pass

    # Fallback to compressed jsonl.gz
    json_path = out_path.with_suffix(".jsonl.gz")
    with gzip.open(json_path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Tải dữ liệu lịch sử nến đa khung thời gian phục vụ backtest.")
    parser.add_argument("--symbols", nargs="+", default=["EURUSD", "XAUUSD"], help="Danh sách symbols cơ sở")
    parser.add_argument("--timeframes", nargs="+", default=["M1", "M5", "M15", "H1", "H4", "D1"], help="Khung thời gian")
    parser.add_argument("--years", type=float, default=2.0, help="Số năm lịch sử cần tải (mặc định: 2)")
    parser.add_argument("--output-dir", default="backtest/data/historical_2y", help="Thư mục lưu trữ")
    args = parser.parse_args()

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    print(f"=== KẾT NỐI MT5 BRIDGE: {cfg.mt5_host}:{cfg.mt5_port} ===")
    if not client.connect():
        print("[ERROR] Không thể kết nối tới MT5 server bridge!")
        return 1

    try:
        account = client.account_info()
        if not account or not account.get("login"):
            print("\n[CHÚ Ý] MT5 chưa đăng nhập tài khoản!")
            print("Vui lòng mở trình duyệt truy cập: http://<IP_SERVER>:8080 (Password: slnctrz123)")
            print("Đăng nhập tài khoản Demo của Anh vào MT5, sau đó chạy lại lệnh này.")
            return 2

        print(f"Đã kết nối tài khoản: {account.get('name')} | Login: {account.get('login')} | Server: {account.get('server')}")

        available_symbols = client.symbols()
        constants = client.constants()
        out_dir = Path(args.output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(UTC)
        start_time = now - timedelta(days=int(args.years * 365.25))
        manifest = {
            "created_at_utc": now.isoformat(),
            "start_time_utc": start_time.isoformat(),
            "end_time_utc": now.isoformat(),
            "account": {"login": account.get("login"), "server": account.get("server")},
            "datasets": {},
        }

        for base_sym in args.symbols:
            actual = resolve_symbol(base_sym, available_symbols)
            if not actual:
                print(f"[ERROR] Không tìm thấy symbol tương ứng cho {base_sym} trên broker!")
                continue

            print(f"\n--- Đang tải dữ liệu cho {base_sym} (Broker alias: {actual}) ---")
            manifest["datasets"][base_sym] = {"actual_symbol": actual, "timeframes": {}}

            for tf in args.timeframes:
                if tf not in constants:
                    print(f"  [SKIP] Khung {tf} không hợp lệ")
                    continue
                tf_int = constants[tf]
                print(f"  Đang tải khung {tf} từ {start_time.date()} đến {now.date()}...")
                rows = fetch_symbol_timeframe_history(client, actual, tf_int, start_time, now)

                out_file = out_dir / f"{base_sym}_{tf}.parquet"
                save_dataset(rows, out_file)
                first_date = datetime.fromtimestamp(rows[0]["time"], UTC).strftime("%Y-%m-%d") if rows else None
                last_date = datetime.fromtimestamp(rows[-1]["time"], UTC).strftime("%Y-%m-%d") if rows else None
                print(f"  -> Hoàn thành {tf}: {len(rows):,} nến (Từ {first_date} đến {last_date})")

                manifest["datasets"][base_sym]["timeframes"][tf] = {
                    "count": len(rows),
                    "first_date": first_date,
                    "last_date": last_date,
                    "file": str(out_file.name),
                }

        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\n[THÀNH CÔNG] Toàn bộ dữ liệu 2 năm đã được lưu tại: {out_dir}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
