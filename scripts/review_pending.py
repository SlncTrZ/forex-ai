#!/usr/bin/env python3
from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import datetime, timezone

from forex_ai.advisory.budget_store import SQLiteDailyBudgetStore
from forex_ai.advisory.policy import AdvisoryPolicy, apply_provider_result
from forex_ai.advisory.provider import BudgetPolicy
from forex_ai.advisory.runtime import AdvisoryRuntime, AdvisoryRuntimePolicy
from forex_ai.config import load_llm_config, load_runtime_config
from forex_ai.integration.deepseek_advisory_provider import DeepSeekLegacyAdvisoryProvider
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.intelligence.deepseek import DeepSeekError, load_api_key
from forex_ai.intelligence.deepseek_web import DeepSeekWebReviewer
from forex_ai.journal.db import initialize, log_audit_event
from forex_ai.journal.integration_repository import pending_v1_candidates, persist_advisory
from forex_ai.mt5.client import MT5Client
from forex_ai.strategy.v1.contracts import fingerprint

UTC = timezone.utc


def _base_symbol(actual: str, configured: tuple[str, ...]) -> str | None:
    upper = actual.upper()
    for base in configured:
        if upper.startswith(base.upper()):
            return base
    return None


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    llm_cfg = load_llm_config()
    if not bool(llm_cfg.get("enabled", False)):
        print(json.dumps({"status": "disabled"}))
        return 0

    now = datetime.now(UTC)
    pending = pending_v1_candidates(cfg.db_path, now_utc=now, limit=3)
    if not pending:
        print(json.dumps({"status": "ok", "reviewed": 0, "source": "candidate_decisions"}))
        return 0

    try:
        load_api_key()
    except DeepSeekError as exc:
        print(json.dumps({"status": "missing_api_key", "error": str(exc)}))
        return 3

    client = MT5Client(cfg)
    if not client.connect():
        print(json.dumps({"status": "mt5_initialize_failed"}))
        return 2

    try:
        macro_context: dict[str, dict] = {}
        eligible = []
        skipped = []
        for candidate in pending:
            base = _base_symbol(candidate.symbol, cfg.symbols)
            if base is None:
                skipped.append({"candidate_id": candidate.candidate_id, "reason": "UNMAPPED_SYMBOL"})
                continue
            context = build_symbol_context(client, cfg, base)
            context["v1_candidate"] = asdict(candidate)
            context["legacy_signal"] = None
            macro_context[candidate.candidate_id] = context
            eligible.append(candidate)

        if not eligible:
            print(json.dumps({"status": "ok", "reviewed": 0, "skipped": skipped}, ensure_ascii=False))
            return 0

        reviewer = DeepSeekWebReviewer()
        provider = DeepSeekLegacyAdvisoryProvider(reviewer)
        invocation = llm_cfg.get("invocation", {})
        response_cfg = llm_cfg.get("response", {})
        max_calls = int(invocation.get("max_calls_per_day", 100))
        max_cost = float(invocation.get("max_cost_usd_per_day", 0.25))
        max_tokens_per_call = int(response_cfg.get("max_tokens", 4000)) * 2
        config_fp = fingerprint(llm_cfg)
        budget_store = SQLiteDailyBudgetStore(
            cfg.db_path,
            provider.provider_id,
            reviewer.model,
            config_fp,
        )
        runtime = AdvisoryRuntime(
            provider=provider,
            provider_model_id=reviewer.model,
            policy=AdvisoryRuntimePolicy(
                budget=BudgetPolicy(
                    max_calls=max_calls,
                    max_tokens=max_calls * max_tokens_per_call,
                    max_cost=max_cost,
                ),
                cache_ttl_seconds=120,
                max_batch_candidates=3,
            ),
            budget_store=budget_store,
        )
        estimated_cost = max_cost / max(max_calls, 1)
        results = runtime.review_batch(
            tuple(eligible),
            macro_context=macro_context,
            macro_cache_key=f"v1:{now.strftime('%Y-%m-%dT%H')}",
            now_utc=now,
            config_fingerprint=config_fp,
            estimated_tokens=max_tokens_per_call * len(eligible),
            estimated_cost=estimated_cost,
        )

        policy = AdvisoryPolicy(default_ttl_seconds=120)
        reviewed = []
        for candidate, result in zip(eligible, results, strict=True):
            advisory = apply_provider_result(
                candidate_id=candidate.candidate_id,
                result=result,
                now_utc=now,
                policy=policy,
            )
            if advisory.expires_at_utc > candidate.expires_at_utc:
                advisory = replace(advisory, expires_at_utc=candidate.expires_at_utc)
            persist_advisory(cfg.db_path, advisory, created_at_utc=now)
            log_audit_event(
                cfg.db_path,
                event_type="V1_ADVISORY_REVIEWED",
                source="advisory_runtime",
                symbol=candidate.symbol,
                entity_id=candidate.candidate_id,
                correlation_id=candidate.correlation_id,
                market_time_msc=candidate.market_time_msc,
                payload={
                    "action": advisory.action.value,
                    "status": advisory.status.value,
                    "reason_code": advisory.reason_code,
                    "risk_multiplier": advisory.risk_multiplier,
                    "model_fingerprint": advisory.model_fingerprint,
                    "legacy_bridge": True,
                },
            )
            reviewed.append({
                "candidate_id": candidate.candidate_id,
                "action": advisory.action.value,
                "status": advisory.status.value,
                "reason_code": advisory.reason_code,
            })

        print(json.dumps({"status": "ok", "reviewed": reviewed, "skipped": skipped}, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
