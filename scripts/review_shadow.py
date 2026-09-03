#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import uuid

from forex_ai.config import load_llm_config, load_runtime_config
from forex_ai.intelligence.budget import get_budget_status
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.intelligence.deepseek import DeepSeekError
from forex_ai.intelligence.deepseek_web import DeepSeekWebReviewer
from forex_ai.intelligence.prompts import PROMPT_VERSION
from forex_ai.journal.db import log_audit_event
from forex_ai.journal.repository import get_signal, insert_llm_decision
from forex_ai.mt5.client import MT5Client


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one DeepSeek shadow review. No order execution.")
    parser.add_argument("symbol", choices=["XAUUSD", "EURUSD", "GBPUSD"])
    parser.add_argument("--signal-id", type=int, default=None)
    parser.add_argument("--correlation-id", default=None)
    args = parser.parse_args()

    cfg = load_runtime_config()
    llm_cfg = load_llm_config()
    budget = get_budget_status(cfg.db_path, llm_cfg)
    if not budget.allowed:
        print(json.dumps({"status": "budget_blocked", "budget": budget.__dict__}, ensure_ascii=False))
        return 4
    client = MT5Client(cfg)
    if not client.connect():
        return 2

    correlation_id = args.correlation_id or f"shadow-{uuid.uuid4().hex[:20]}"
    try:
        context = build_symbol_context(client, cfg, args.symbol)
        if args.signal_id is not None:
            signal = get_signal(cfg.db_path, args.signal_id)
            if signal is None:
                print(json.dumps({"status": "signal_not_found", "signal_id": args.signal_id}))
                return 5
            context["candidate_signal"] = signal
            correlation_id = args.correlation_id or signal.get("signal_key") or correlation_id
        market_time_msc = context.get("tick", {}).get("time_msc")
        log_audit_event(
            cfg.db_path,
            event_type="LLM_CONTEXT",
            source="llm",
            symbol=context.get("symbol"),
            correlation_id=correlation_id,
            market_time_msc=market_time_msc,
            payload={"signal_id": args.signal_id, "initial_context": context},
        )

        reviewer = DeepSeekWebReviewer()
        try:
            decision, usage, raw_hash = reviewer.review(context)
        except Exception as exc:
            log_audit_event(
                cfg.db_path,
                event_type="LLM_ERROR",
                source="llm",
                symbol=context.get("symbol"),
                correlation_id=correlation_id,
                market_time_msc=market_time_msc,
                payload={
                    "signal_id": args.signal_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "response_id": reviewer.last_response_id,
                    "usage": reviewer.last_usage.model_dump() if reviewer.last_usage else None,
                    "web_trace": reviewer.last_web_trace,
                },
            )
            raise

        if reviewer.last_web_trace:
            log_audit_event(
                cfg.db_path,
                event_type="LLM_WEB_TRACE",
                source="llm",
                symbol=context.get("symbol"),
                correlation_id=correlation_id,
                market_time_msc=market_time_msc,
                payload={
                    "signal_id": args.signal_id,
                    "response_id": reviewer.last_response_id,
                    "web_trace": reviewer.last_web_trace,
                },
            )

        decision_id = insert_llm_decision(
            cfg.db_path,
            symbol=context["symbol"],
            mode="SHADOW",
            model=reviewer.model,
            prompt_version=PROMPT_VERSION,
            decision=decision.model_dump(),
            usage=usage.model_dump(),
            signal_id=args.signal_id,
            raw_response_hash=raw_hash,
            correlation_id=correlation_id,
            market_time_msc=market_time_msc,
        )
        print(
            json.dumps(
                {
                    "status": "ok",
                    "mode": "SHADOW",
                    "correlation_id": correlation_id,
                    "decision_id": decision_id,
                    "decision": decision.model_dump(),
                    "usage": usage.model_dump(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except DeepSeekError as exc:
        print(json.dumps({"status": "deepseek_error", "error": str(exc)}, ensure_ascii=False))
        return 3
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
