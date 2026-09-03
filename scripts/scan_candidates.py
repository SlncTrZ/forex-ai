#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from forex_ai.config import load_runtime_config
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.journal.db import initialize, log_audit_event, session
from forex_ai.journal.integration_repository import persist_candidate
from forex_ai.journal.repository import insert_signal
from forex_ai.mt5.client import MT5Client
from forex_ai.mt5.symbols import resolve_symbol
from forex_ai.runtime.ops import assess_runtime_health
from forex_ai.strategy.signal_engine import generate_signal
from forex_ai.strategy.v1.live_scan import build_market_from_mt5_rows, evaluate_v1_market, scan_result_payload

UTC = timezone.utc


def _already_scanned(db_path, scan_key: str) -> bool:
    with session(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM audit_events WHERE source='production_v1_scanner' AND entity_id=? "
            "AND event_type IN ('V1_CANDIDATE_ACCEPTED','V1_STRATEGY_REJECTED') LIMIT 1",
            (scan_key,),
        ).fetchone()
    return row is not None


def _v1_scan_symbol(client: MT5Client, cfg, base_symbol: str, *, now_utc: datetime) -> list[dict]:
    available = client.symbols()
    actual = resolve_symbol(base_symbol, available)
    if actual is None:
        raise ValueError(f"Unable to resolve broker symbol for {base_symbol}")
    constants = client.constants()
    tick = client.tick(actual)
    if not tick:
        raise RuntimeError(f"TICK_UNAVAILABLE:{actual}")
    bars_by_timeframe = {
        label: client.bars(actual, constants[label], 80)
        for label in ("M15", "H1", "H4")
    }
    if any(len(rows) < 2 for rows in bars_by_timeframe.values()):
        raise RuntimeError(f"BARS_UNAVAILABLE:{actual}")
    market = build_market_from_mt5_rows(
        symbol=actual,
        tick_raw=tick,
        bars_by_timeframe=bars_by_timeframe,
        captured_at_utc=now_utc,
    )
    latest_closed_m15 = market.timeframes["M15"].closed_bars[-1].time_utc
    output: list[dict] = []
    for row in evaluate_v1_market(market, now_utc=now_utc):
        scan_key = f"{row.strategy_id}:{actual}:{int(latest_closed_m15.timestamp())}"
        if _already_scanned(cfg.db_path, scan_key):
            output.append({"strategy": row.strategy_id, "status": "ALREADY_SCANNED", "scan_key": scan_key})
            continue
        payload = scan_result_payload(row)
        payload["base_symbol"] = base_symbol
        payload["symbol"] = actual
        payload["market_snapshot_fingerprint"] = market.decision_fingerprint
        payload["scan_key"] = scan_key
        payload["closed_m15_time_utc"] = latest_closed_m15.isoformat()
        if row.result.candidate is not None:
            persist_candidate(cfg.db_path, row.result.candidate)
            log_audit_event(
                cfg.db_path,
                event_type="V1_CANDIDATE_ACCEPTED",
                source="production_v1_scanner",
                symbol=actual,
                entity_id=scan_key,
                correlation_id=row.result.candidate.correlation_id,
                market_time_msc=market.market_time_msc,
                payload=payload,
            )
            output.append({"strategy": row.strategy_id, "status": "ACCEPTED", "candidate_id": row.result.candidate.candidate_id})
        else:
            log_audit_event(
                cfg.db_path,
                event_type="V1_STRATEGY_REJECTED",
                source="production_v1_scanner",
                symbol=actual,
                entity_id=scan_key,
                market_time_msc=market.market_time_msc,
                payload=payload,
            )
            output.append({"strategy": row.strategy_id, "status": "REJECTED", "reason_codes": list(row.result.no_setup_reason_codes)})
    return output


def main() -> int:
    cfg = load_runtime_config()
    initialize(cfg.db_path)
    health = assess_runtime_health(cfg.db_path)
    if not health.healthy:
        log_audit_event(
            cfg.db_path,
            event_type="V1_SCAN_BLOCKED",
            source="production_v1_scanner",
            payload={"reasons": list(health.reasons), "latest_heartbeat_state": health.latest_heartbeat_state},
        )
        print(json.dumps({"status": "blocked", "reasons": list(health.reasons)}))
        return 3

    client = MT5Client(cfg)
    try:
        if not client.connect():
            print(json.dumps({"status": "mt5_initialize_failed"}))
            return 2
        output = []
        now = datetime.now(UTC)
        legacy_enabled = os.getenv("FOREX_AI_LEGACY_SCAN", "false").lower() in {"1", "true", "yes"}
        for base_symbol in cfg.symbols:
            legacy_row = None
            if legacy_enabled:
                context = build_symbol_context(client, cfg, base_symbol)
                legacy = generate_signal(context)
                if legacy is not None:
                    signal_id, created = insert_signal(
                        cfg.db_path,
                        signal_key=legacy.signal_key,
                        symbol=legacy.symbol,
                        strategy=legacy.strategy,
                        direction=legacy.direction,
                        score=legacy.score,
                        proposed_entry=legacy.proposed_entry,
                        proposed_sl=legacy.proposed_sl,
                        proposed_tp=legacy.proposed_tp,
                        rr=legacy.rr,
                        payload=legacy.evidence,
                        market_time_msc=legacy.market_time_msc,
                    )
                    legacy_row = {
                        "signal_id": signal_id,
                        "created": created,
                        "strategy": legacy.strategy,
                        "direction": legacy.direction,
                        "score": legacy.score,
                    }
            try:
                v1 = _v1_scan_symbol(client, cfg, base_symbol, now_utc=now)
            except Exception as exc:
                log_audit_event(
                    cfg.db_path,
                    event_type="V1_SCAN_ERROR",
                    source="production_v1_scanner",
                    symbol=None,
                    payload={"base_symbol": base_symbol, "error": f"{type(exc).__name__}: {exc}"},
                )
                v1 = [{"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}]
            output.append({"base_symbol": base_symbol, "legacy": legacy_row, "production_v1": v1})
        print(json.dumps({"status": "ok", "results": output}, ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
