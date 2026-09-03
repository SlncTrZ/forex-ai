#!/usr/bin/env python3
from __future__ import annotations

import json

from forex_ai.config import load_runtime_config
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.journal.db import initialize
from forex_ai.journal.repository import insert_signal
from forex_ai.mt5.client import MT5Client
from forex_ai.strategy.signal_engine import generate_signal


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    client = MT5Client(cfg)
    try:
        if not client.connect():
            print(json.dumps({"status": "mt5_initialize_failed"}))
            return 2
        output = []
        for base_symbol in cfg.symbols:
            context = build_symbol_context(client, cfg, base_symbol)
            candidate = generate_signal(context)
            if candidate is None:
                output.append({"base_symbol": base_symbol, "candidate": None})
                continue
            signal_id, created = insert_signal(
                cfg.db_path,
                signal_key=candidate.signal_key,
                symbol=candidate.symbol,
                strategy=candidate.strategy,
                direction=candidate.direction,
                score=candidate.score,
                proposed_entry=candidate.proposed_entry,
                proposed_sl=candidate.proposed_sl,
                proposed_tp=candidate.proposed_tp,
                rr=candidate.rr,
                payload=candidate.evidence,
                market_time_msc=candidate.market_time_msc,
            )
            output.append(
                {
                    "base_symbol": base_symbol,
                    "signal_id": signal_id,
                    "created": created,
                    "candidate": {
                        "symbol": candidate.symbol,
                        "strategy": candidate.strategy,
                        "direction": candidate.direction,
                        "score": candidate.score,
                        "signal_key": candidate.signal_key,
                    },
                }
            )
        print(json.dumps({"status": "ok", "results": output}, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
