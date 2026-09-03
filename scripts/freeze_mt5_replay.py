#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from forex_ai.config import load_runtime_config
from forex_ai.mt5.client import MT5Client
from forex_ai.research.dataset import freeze_replay_dataset
from forex_ai.research.mt5_dataset import build_replay_events_from_mt5_bars

UTC = timezone.utc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, help="Configured base symbol, e.g. EURUSD")
    parser.add_argument("--output", required=True)
    parser.add_argument("--m15-count", type=int, default=5000)
    parser.add_argument("--h1-count", type=int, default=2000)
    parser.add_argument("--h4-count", type=int, default=1000)
    parser.add_argument("--history-bars", type=int, default=60)
    args = parser.parse_args()

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    if not client.connect():
        raise RuntimeError("MT5_CONNECT_FAILED")
    try:
        symbols = client.symbols()
        names = {str(row.get("name")) for row in symbols}
        actual = args.symbol if args.symbol in names else None
        if actual is None:
            matches = sorted(name for name in names if name.startswith(args.symbol))
            if len(matches) != 1:
                raise RuntimeError(f"SYMBOL_MAPPING_UNRESOLVED:{args.symbol}:{matches}")
            actual = matches[0]
        info = client.symbol_info(actual)
        if not info:
            raise RuntimeError(f"SYMBOL_INFO_UNAVAILABLE:{actual}")
        constants = client.constants()
        m15 = client.bars(actual, constants["M15"], args.m15_count)
        h1 = client.bars(actual, constants["H1"], args.h1_count)
        h4 = client.bars(actual, constants["H4"], args.h4_count)
        events = build_replay_events_from_mt5_bars(
            symbol=actual,
            point=float(info["point"]),
            m15_rows=m15,
            h1_rows=h1,
            h4_rows=h4,
            history_bars=args.history_bars,
        )
        if not events:
            raise RuntimeError("NO_REPLAY_EVENTS")
        source_id = (
            f"mt5:{actual}:broker={cfg.mt5_host}:{cfg.mt5_port}:"
            f"m15={len(m15)}:h1={len(h1)}:h4={len(h4)}:history={args.history_bars}"
        )
        manifest = freeze_replay_dataset(
            events,
            data_path=Path(args.output).expanduser(),
            source_id=source_id,
            created_at_utc=datetime.now(UTC),
        )
        data_path = Path(args.output).expanduser()
        manifest_path = data_path.with_suffix(data_path.suffix + ".manifest.json")
        print(f"dataset={data_path}")
        print(f"manifest={manifest_path}")
        print(f"records={manifest.record_count}")
        print(f"dataset_sha256={manifest.dataset_sha256}")
        print(f"event_fingerprint={manifest.event_fingerprint}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
