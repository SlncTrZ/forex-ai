#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from forex_ai.config import load_runtime_config
from forex_ai.journal.db import initialize
from forex_ai.mt5.client import MT5Client
from forex_ai.runtime.resilience import MT5ResyncCoordinator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path)
    parser.add_argument("--client-drop-test", action="store_true")
    args = parser.parse_args()
    cfg = load_runtime_config()
    db_path = args.db or (Path(tempfile.mkdtemp(prefix="forex-ai-resync-")) / "smoke.db")
    initialize(db_path)
    client = MT5Client(cfg)
    coordinator = MT5ResyncCoordinator(client=client, symbols=cfg.symbols, db_path=db_path)
    try:
        outcome = coordinator.sync_once(now_utc=datetime.now(timezone.utc))
        print(f"state={outcome.state.value}")
        print(f"ready={outcome.ready}")
        print(f"reason={outcome.reason}")
        print(f"mapping={dict(outcome.symbol_mapping)}")
        print(f"db={db_path}")
        if not outcome.ready:
            return 2
        if args.client_drop_test:
            client.close()
            degraded = coordinator.sync_once(now_utc=datetime.now(timezone.utc))
            recovered = coordinator.sync_once(now_utc=datetime.now(timezone.utc))
            print(f"after_drop={degraded.state.value}:{degraded.reason}")
            print(f"after_reconnect={recovered.state.value}:{recovered.ready}")
            if degraded.ready or not recovered.ready:
                return 3
        return 0
    finally:
        coordinator.close()


if __name__ == "__main__":
    raise SystemExit(main())
