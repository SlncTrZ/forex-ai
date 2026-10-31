#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from time import perf_counter

from forex_ai.advisory.ollama_safety_gate import D3DecisionSafetyGate, GateUnavailable
from forex_ai.advisory.regime_router import resolve_regime, btc_demo_advisory_regime
from forex_ai.advisory.confluence_scoring import assess_confluence
from forex_ai.config import load_risk_profile, load_runtime_config
from forex_ai.integration.engine import DecisionOrchestrator, production_strategy_bindings, strategy_eligible_for_symbol
from forex_ai.journal.db import initialize, log_audit_event, session
from forex_ai.market.context_config import load_market_context_snapshot
from forex_ai.mt5.client import MT5Client
from forex_ai.risk.account_guard import AccountBindingError, assert_account_matches
from forex_ai.runtime.market_schedule import new_entries_allowed
from forex_ai.runtime.ops import assess_runtime_health
from forex_ai.runtime.resilience import MT5ResyncCoordinator
from forex_ai.runtime.risk_context import build_risk_context
from forex_ai.strategy.config import load_strategy_snapshot, required_raw_bars
from forex_ai.execution.account_mode import MT5AccountTradeMode, detect_account_trade_mode

UTC = timezone.utc
D = Decimal


def _already_scanned(db_path, scan_key: str) -> bool:
    with session(db_path) as con:
        row = con.execute(
            "SELECT 1 FROM audit_events WHERE source='production_v1_scanner' AND entity_id=? "
            "AND event_type IN ('V1_CANDIDATE_ACCEPTED','V1_STRATEGY_REJECTED') LIMIT 1",
            (scan_key,),
        ).fetchone()
    return row is not None


def _safety_with_account_binding(outcome):
    safety = outcome.safety
    if safety is None:
        return None
    reasons = list(safety.blocking_reasons)
    try:
        assert_account_matches(outcome.raw_account or {}, require_binding=True)
    except AccountBindingError as exc:
        reasons.append(exc.reason)
    return safety.model_copy(update={
        "reconciled": safety.reconciled and not reasons,
        "blocking_reasons": tuple(dict.fromkeys(reasons)),
    })


def _btc_demo_account_verified(cfg, raw_account) -> bool:
    """Both operator-selected DEMO config and live broker trade_mode must agree."""
    if cfg.mode != "DEMO":
        return False
    try:
        return detect_account_trade_mode(raw_account or {}) is MT5AccountTradeMode.DEMO
    except RuntimeError:
        return False


def _paired_eligible_decisions(decisions, bindings, symbol):
    """Pair outcomes against the same symbol eligibility applied by the orchestrator."""
    eligible = tuple(binding for binding in bindings
                     if strategy_eligible_for_symbol(binding.config.version.strategy_id, symbol))
    return zip(decisions, eligible, strict=True)


def _scan_symbol(*, client: MT5Client, cfg, outcome, base_symbol: str, orchestrator: DecisionOrchestrator, constants: dict) -> list[dict]:
    started = perf_counter()
    market = outcome.markets[base_symbol]
    actual = outcome.symbol_mapping[base_symbol]
    broker = outcome.broker_state
    safety = _safety_with_account_binding(outcome)
    if broker is None or safety is None:
        raise RuntimeError("BROKER_STATE_UNAVAILABLE")

    contracts = {item.symbol: item for item in broker.contracts}
    ticks = {item.symbol: item for item in broker.ticks}
    contract = contracts[actual]
    tick = ticks[actual]
    now = datetime.now(UTC)
    context = build_risk_context(cfg.db_path, broker, now_utc=now)
    tick_age = max(D("0"), D(str((now.timestamp() * 1000 - tick.time_msc) / 1000.0)))
    context = replace(context, tick_age_seconds=tick_age)

    order_types = {"BUY": constants["ORDER_TYPE_BUY"], "SELL": constants["ORDER_TYPE_SELL"]}

    def calc_profit(side: str, symbol: str, volume: D, open_price: D, close_price: D) -> D:
        value = client.order_calc_profit(order_types[side], symbol, float(volume), float(open_price), float(close_price))
        if value is None:
            raise RuntimeError("order_calc_profit returned None")
        return D(str(value))

    def calc_margin(side: str, symbol: str, volume: D, open_price: D) -> D:
        value = client.order_calc_margin(order_types[side], symbol, float(volume), float(open_price))
        if value is None:
            raise RuntimeError("order_calc_margin returned None")
        return D(str(value))

    # Active strategy admission: exactly one cached D3 regime decision per
    # symbol/H1 closed-bar fingerprint, before candidate risk assessment.
    try:
        d3 = D3DecisionSafetyGate.from_environment()
    except GateUnavailable:
        d3 = None
    regime = resolve_regime(market, db_path=cfg.db_path, provider=d3)
    # Broker trade_mode is authoritative. Config DEMO alone is never enough.
    broker_demo = _btc_demo_account_verified(cfg, outcome.raw_account)
    admission_regime = btc_demo_advisory_regime(
        regime, broker_demo_verified=broker_demo,
        strategy_id="btc_squeeze_breakout_m15", symbol=actual,
    )
    if admission_regime is not regime:
        log_audit_event(
            cfg.db_path, event_type="BTC_DEMO_REGIME_ADVISORY_APPLIED",
            source="production_v1_scanner", symbol=actual,
            entity_id=regime.feature_fingerprint,
            payload={"policy_version": regime.policy_version,
                     "classifier_reason": regime.reason_code,
                     "admission_reason": admission_regime.reason_code,
                     "confidence": regime.confidence,
                     "choice_probability": regime.choice_probability,
                     "feature_fingerprint": regime.feature_fingerprint,
                     "model_fingerprint": regime.model_fingerprint},
        )
    entry_window_open = new_entries_allowed(now, actual)
    decisions = orchestrator.scan(
        market,
        account=broker.account,
        contract=contract,
        tick=tick,
        safety=safety,
        risk_context=context,
        calc_profit=calc_profit,
        calc_margin=calc_margin,
        now_utc=now,
        deterministic_gate_ok=entry_window_open,
        deterministic_gate_reason="WEEKEND_ENTRY_CUTOFF",
        regime_decision=admission_regime,
        confluence_for=lambda result, decision: assess_confluence(
            result, decision, db_path=cfg.db_path, provider=d3,
        ),
    )

    latest_closed_m15 = market.timeframes["M15"].closed_bars[-1].time_utc
    output: list[dict] = []
    for decision, binding in _paired_eligible_decisions(decisions, orchestrator.strategies, actual):
        row = decision.strategy_result
        candidate = row.candidate
        strategy_id = binding.config.version.strategy_id
        strategy_version = binding.config.version.version
        if candidate is not None and candidate.opportunity_key:
            scan_key = candidate.opportunity_key
        else:
            tf_name = str(binding.config.parameters.get("decision_timeframe", "M15"))
            strat_tf = market.timeframes.get(tf_name) or market.timeframes.get("M15")
            latest_bar_t = strat_tf.closed_bars[-1].time_utc if (strat_tf and strat_tf.closed_bars) else now
            scan_key = f"{strategy_id}:{strategy_version}:{actual}:{int(latest_bar_t.timestamp())}"
        if _already_scanned(cfg.db_path, scan_key):
            output.append({"strategy": strategy_id, "status": "ALREADY_SCANNED", "scan_key": scan_key})
            continue

        payload = {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_config_fingerprint": binding.config.fingerprint,
            "candidate": None if candidate is None else candidate.__dict__,
            "reason_codes": list(row.no_setup_reason_codes),
            "evidence": {"reason_codes": list(row.evidence.reason_codes), "values": dict(row.evidence.values)},
            "base_symbol": base_symbol,
            "symbol": actual,
            "market_snapshot_fingerprint": market.decision_fingerprint,
            "scan_key": scan_key,
            "closed_m15_time_utc": latest_closed_m15.isoformat(),
            "market_captured_at_utc": market.captured_at_utc.isoformat(),
            "strategy_evaluated_at_utc": now.isoformat(),
            "risk_approved": decision.risk_result.approved if decision.risk_result is not None else False,
            "regime": regime.regime,
            "regime_reason": regime.reason_code,
            "regime_admission_reason": admission_regime.reason_code,
            "confluence_score": decision.confluence.score if decision.confluence else None,
            "confluence_reason": decision.confluence.reason_code if decision.confluence else None,
            "confluence_risk_multiplier": decision.confluence.risk_multiplier if decision.confluence else None,
            "regime_feature_fingerprint": regime.feature_fingerprint,
            "regime_model_fingerprint": regime.model_fingerprint,
            "risk_reason_codes": list(decision.risk_result.reason_codes) if decision.risk_result is not None else list(decision.blocked_reasons),
            "risk_profile_fingerprint": decision.risk_result.risk_profile_fingerprint if decision.risk_result is not None else None,
            "safety_snapshot_fingerprint": safety.fingerprint,
            "safety_blocking_reasons": list(safety.blocking_reasons),
            "higher_timeframe_structure": market.context.get("higher_timeframe_structure"),
        }
        event_type = "V1_CANDIDATE_ACCEPTED" if candidate is not None else "V1_STRATEGY_REJECTED"
        log_audit_event(
            cfg.db_path,
            event_type=event_type,
            source="production_v1_scanner",
            symbol=actual,
            entity_id=scan_key,
            correlation_id=candidate.correlation_id if candidate is not None else None,
            market_time_msc=market.market_time_msc,
            payload=payload,
        )
        output.append({
            "strategy": strategy_id,
            "status": "ACCEPTED" if candidate is not None else "REJECTED",
            "candidate_id": candidate.candidate_id if candidate is not None else None,
            "opportunity_key": candidate.opportunity_key if candidate is not None else None,
            "risk_approved": payload["risk_approved"],
            "risk_reason_codes": payload["risk_reason_codes"],
            "regime": regime.regime,
            "regime_reason": regime.reason_code,
            "regime_admission_reason": admission_regime.reason_code,
            "confluence_score": decision.confluence.score if decision.confluence else None,
        })

    latency_ms = int((perf_counter() - started) * 1000)
    log_audit_event(
        cfg.db_path,
        event_type="V1_SYMBOL_SCAN_LATENCY",
        source="production_v1_scanner",
        symbol=actual,
        entity_id=base_symbol,
        payload={"base_symbol": base_symbol, "symbol": actual, "latency_ms": latency_ms},
    )
    for item in output:
        item["latency_ms"] = latency_ms
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
        return 0

    strategy_snapshot = load_strategy_snapshot()
    market_context_snapshot = load_market_context_snapshot()
    scan_started = perf_counter()
    if strategy_snapshot.loaded_from_last_good:
        log_audit_event(
            cfg.db_path, event_type="STRATEGY_CONFIG_RELOAD_REJECTED",
            source="production_v1_scanner", entity_id=strategy_snapshot.fingerprint,
            payload={"source_path": str(strategy_snapshot.source_path),
                     "rejected_error": strategy_snapshot.rejected_error,
                     "active_fingerprint": strategy_snapshot.fingerprint},
        )
    if market_context_snapshot.loaded_from_last_good:
        log_audit_event(
            cfg.db_path, event_type="MARKET_CONTEXT_CONFIG_RELOAD_REJECTED",
            source="production_v1_scanner", entity_id=market_context_snapshot.fingerprint,
            payload={"source_path": str(market_context_snapshot.source_path),
                     "rejected_error": market_context_snapshot.rejected_error,
                     "active_fingerprint": market_context_snapshot.fingerprint},
        )
    orchestrator = DecisionOrchestrator(
        db_path=cfg.db_path, risk_profile=load_risk_profile(),
        strategies=production_strategy_bindings(strategy_snapshot),
    )
    output = []
    blocked = 0
    for base_symbol in cfg.symbols:
        # A closed XAU session cannot abort a separately live BTC scan.
        # Each coordinator owns/cleans up its own MT5 connection and heartbeat.
        client = MT5Client(cfg)
        coordinator = MT5ResyncCoordinator(
            client=client, symbols=(base_symbol,), db_path=cfg.db_path,
            bars_count=max(261, required_raw_bars(strategy_snapshot, production_only=False)),
            load_history=False, market_context=market_context_snapshot,
        )
        try:
            outcome = coordinator.sync_once(now_utc=datetime.now(UTC))
            if not outcome.ready:
                blocked += 1
                raw_reason = str(outcome.reason or "")
                safe_reason = (raw_reason if re.fullmatch(r"[A-Z][A-Z0-9_]*(?::[A-Za-z0-9_.-]{1,64}){0,3}", raw_reason)
                               else "SYMBOL_SYNC_FAILED")
                log_audit_event(
                    cfg.db_path, event_type="V1_SYMBOL_SYNC_BLOCKED",
                    source="production_v1_scanner", symbol=base_symbol,
                    payload={"base_symbol": base_symbol, "reason": safe_reason},
                )
                output.append({"base_symbol": base_symbol, "status": "sync_blocked",
                               "reason": safe_reason, "production_v1": []})
                continue
            v1 = _scan_symbol(
                client=client, cfg=cfg, outcome=outcome,
                base_symbol=base_symbol, orchestrator=orchestrator,
                constants=client.constants(),
            )
            output.append({"base_symbol": base_symbol, "status": "scanned", "production_v1": v1})
        except Exception as exc:
            blocked += 1
            log_audit_event(
                cfg.db_path, event_type="V1_SCAN_ERROR",
                source="production_v1_scanner", symbol=base_symbol,
                payload={"base_symbol": base_symbol, "error_type": type(exc).__name__},
            )
            output.append({"base_symbol": base_symbol, "status": "error",
                           "error_type": type(exc).__name__, "production_v1": []})
        finally:
            coordinator.close()
    total_latency_ms = int((perf_counter() - scan_started) * 1000)
    log_audit_event(
        cfg.db_path, event_type="V1_SCAN_LATENCY",
        source="production_v1_scanner",
        payload={"total_latency_ms": total_latency_ms, "symbol_count": len(cfg.symbols),
                 "blocked_symbols": blocked},
    )
    status = "sync_blocked" if blocked == len(cfg.symbols) else "partial" if blocked else "ok"
    print(json.dumps({"status": status, "total_latency_ms": total_latency_ms,
                      "results": output}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
