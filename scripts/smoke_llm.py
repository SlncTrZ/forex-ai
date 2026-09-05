#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from forex_ai.config import load_runtime_config
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.intelligence.prompts import PROMPT_VERSION
from forex_ai.intelligence.reviewer import MockReviewer
from forex_ai.journal.repository import insert_llm_decision
from forex_ai.mt5.client import MT5Client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", choices=["XAUUSD", "EURUSD"])
    args = parser.parse_args()

    cfg = load_runtime_config()
    client = MT5Client(cfg)
    provider = MockReviewer()
    try:
        if not client.connect():
            return 2
        context = build_symbol_context(client, cfg, args.symbol)
        decision, usage = provider.review(context)
        decision_id = insert_llm_decision(
            cfg.db_path,
            symbol=context["symbol"],
            mode=cfg.mode,
            model=provider.model,
            prompt_version=PROMPT_VERSION,
            decision=decision.model_dump(),
            usage=usage.model_dump(),
        )
        print(json.dumps({
            "decision_id": decision_id,
            "provider": provider.name,
            "model": provider.model,
            "decision": decision.model_dump(),
            "usage": usage.model_dump(),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
