"""Read-only per-symbol closed-H1 intelligence, independent of candidate scans.

No owner-control, execution, order or risk imports. Audit is the only side effect.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from forex_ai.advisory.ollama_safety_gate import D3DecisionSafetyGate
from forex_ai.advisory.regime_router import POLICY_VERSION, REGIME_CONFIDENCE_FLOOR, _decision, quant_features
from forex_ai.integration.adapters import timeframe_snapshot
from forex_ai.journal.db import log_audit_event, session
from forex_ai.runtime.market_schedule import new_entries_allowed
from forex_ai.strategy.v1.contracts import MarketSnapshot, TimeframeSnapshot, fingerprint

UTC = timezone.utc
EVENT = "MARKET_INTELLIGENCE_OBSERVATION"
DECISION_EVENT = "MARKET_INTELLIGENCE_DECISION"
SOURCE = "market_intelligence_h1"
MAX_TICK_AGE = 15
RETRY_SECONDS = 300


def _closed_h1(rows: Iterable[dict], now: datetime) -> TimeframeSnapshot:
    """MT5 bar.time is bar OPEN, and some off-session requests omit a forming bar."""
    ordered = sorted(rows, key=lambda x: int(x["time"]))
    tf = timeframe_snapshot("H1", ordered)
    if tf.current_bar and tf.current_bar.time_utc + timedelta(hours=1) <= now:
        tf = TimeframeSnapshot("H1", (*tf.closed_bars, tf.current_bar))
    return tf


def _audit_observation(db_path: Path, response: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A fresh broker observation each five-minute bucket, even on D3 cache hit.

    Unlike the immutable D3 decision this heartbeat proves the worker is alive.
    No execution service consumes this event.
    """
    bucket = int(now.timestamp()) // RETRY_SECONDS
    key = hashlib.sha256(
        f"{EVENT}:{response['symbol']}:{response.get('market_state')}:"
        f"{response.get('feature_fingerprint')}:{bucket}".encode()
    ).hexdigest()
    with session(db_path) as con:
        existing = con.execute(
            "SELECT 1 FROM audit_events WHERE event_type=? AND entity_id=? LIMIT 1", (EVENT, key)
        ).fetchone()
    if not existing:
        log_audit_event(db_path, event_type=EVENT, source=SOURCE, symbol=response["symbol"],
                        entity_id=key, payload=response)
    return response


def observe_symbol(
    symbol: str, *, rows: list[dict], tick: dict | None,
    provider: D3DecisionSafetyGate | None, db_path: Path, now_utc: datetime,
) -> dict[str, Any]:
    """Persist a sanitized observation, never an execution authorization."""
    now = now_utc.astimezone(UTC)
    tf = _closed_h1(rows, now)
    last_close = (tf.closed_bars[-1].time_utc + timedelta(hours=1)) if tf.closed_bars else None
    off_session = not new_entries_allowed(now, symbol)
    tick_time_ms = int((tick or {}).get("time_msc") or (tick or {}).get("time", 0) * 1000)
    tick_age = (now.timestamp() * 1000 - tick_time_ms) / 1000 if tick_time_ms > 0 else None
    fresh_tick = tick_age is not None and -2 <= tick_age <= MAX_TICK_AGE
    market_state = "OFF_SESSION" if off_session else ("ACTIVE" if fresh_tick else "STALE_TICK")
    model_fp = provider.fingerprint if provider else "D3_UNCONFIGURED"
    closed_iso = last_close.isoformat() if last_close else None
    response = {
        "symbol": symbol, "timeframe": "H1", "market_state": market_state,
        "closed_h1_utc": closed_iso, "quant_regime": "UNCERTAIN",
        "regime": "UNCERTAIN", "model_regime": None, "confidence": None,
        "choice_probability": None, "reason_code": "REGIME_H1_DATA_UNAVAILABLE",
        "feature_fingerprint": "", "model_fingerprint": model_fp,
        "latency_ms": 0, "policy_version": POLICY_VERSION,
        "tick_age_seconds": round(tick_age, 1) if tick_age is not None else None,
        "observed_at": now.isoformat(), "confirmed_for_trading": False,
    }
    if off_session:
        response["reason_code"] = "OFF_SESSION"
    elif not fresh_tick:
        response["reason_code"] = "STALE_TICK"
    elif len(tf.closed_bars) < 220:
        response["reason_code"] = "INSUFFICIENT_H1_BARS"
    else:
        price = float((tick or {}).get("bid") or 0)
        ask = float((tick or {}).get("ask") or 0)
        if price > 0 and ask >= price:
            market = MarketSnapshot(
                symbol, now, tick_time_ms, price, ask, {"H1": tf},
            )
            features = quant_features(market)
            if features is not None:
                response["quant_regime"] = features.quant_regime
                response["feature_fingerprint"] = features.fingerprint
                response["reason_code"] = "REGIME_QUANT_UNCERTAIN" if features.quant_regime == "UNCERTAIN" else "REGIME_D3_UNAVAILABLE"
                # Avoid repeat inference within the H1 context; never cache stale
                # provider failures for the full lifetime of the closed bar.
                context_key = hashlib.sha256(
                    f"{EVENT}:{symbol}:{features.fingerprint}:{model_fp}:{POLICY_VERSION}".encode()
                ).hexdigest()
                with session(db_path) as con:
                    row = con.execute(
                        "SELECT timestamp_utc,payload_json FROM audit_events WHERE event_type=? AND entity_id=? "
                        "ORDER BY id DESC LIMIT 1", (DECISION_EVENT, context_key),
                    ).fetchone()
                if row:
                    try:
                        previous = json.loads(row["payload_json"])
                        age = (now - datetime.fromisoformat(previous.get("observed_at") or row["timestamp_utc"]).astimezone(UTC)).total_seconds()
                        valid = (previous.get("policy_version") == POLICY_VERSION
                                 and previous.get("symbol") == symbol
                                 and previous.get("feature_fingerprint") == features.fingerprint
                                 and previous.get("model_fingerprint") == model_fp)
                        if previous.get("reason_code") == "REGIME_CONFIRMED":
                            c, p = previous.get("confidence"), previous.get("choice_probability")
                            valid = (valid and previous.get("regime") == features.quant_regime
                                     == previous.get("model_regime") and
                                     type(c) in (float, int) and type(p) in (float, int) and
                                     REGIME_CONFIDENCE_FLOOR <= c <= 1 and
                                     REGIME_CONFIDENCE_FLOOR <= p <= 1)
                        if valid and (previous.get("reason_code") == "REGIME_CONFIRMED"
                                      or 0 <= age < RETRY_SECONDS):
                            # Return current observation with cached decision evidence.
                            for k in ("quant_regime", "regime", "model_regime", "confidence",
                                      "choice_probability", "reason_code", "feature_fingerprint", "latency_ms"):
                                response[k] = previous.get(k)
                            response["confirmed_for_trading"] = False
                            response["cache_hit"] = True
                            return _audit_observation(db_path, response, now)
                    except (ValueError, TypeError, KeyError):
                        pass
                d3 = _decision(features, symbol, provider)
                response.update(asdict(d3))
                response["confirmed_for_trading"] = False
                response["cache_hit"] = False
                log_audit_event(db_path, event_type=DECISION_EVENT, source=SOURCE, symbol=symbol,
                                entity_id=context_key, payload=response)
                return _audit_observation(db_path, response, now)
    # Session errors and insufficient evidence are separate per-symbol audits,
    # throttled by 5-minute buckets rather than every tick.
    response["feature_fingerprint"] = fingerprint({
        "symbol": symbol, "closed_h1_utc": closed_iso, "market_state": market_state,
        "reason_code": response["reason_code"], "policy_version": POLICY_VERSION,
    })
    return _audit_observation(db_path, response, now)


def observe_unavailable(symbol: str, *, reason: str, db_path: Path, now_utc: datetime) -> None:
    """Audit strictly bounded failure codes without capturing exception details."""
    if reason not in ("SYMBOL_UNRESOLVED", "MT5_BARS_UNAVAILABLE", "MT5_TICK_UNAVAILABLE", "OBSERVATION_FAILED"):
        reason = "OBSERVATION_FAILED"
    bucket = int(now_utc.timestamp()) // RETRY_SECONDS
    key = hashlib.sha256(f"{EVENT}:{symbol}:{reason}:{bucket}".encode()).hexdigest()
    with session(db_path) as con:
        existing = con.execute("SELECT 1 FROM audit_events WHERE event_type=? AND entity_id=? LIMIT 1", (EVENT, key)).fetchone()
    if not existing:
        log_audit_event(db_path, event_type=EVENT, source=SOURCE, symbol=symbol, entity_id=key,
                        payload={"symbol": symbol, "timeframe": "H1", "market_state": "UNAVAILABLE",
                                 "regime": "UNCERTAIN", "quant_regime": "UNCERTAIN",
                                 "model_regime": None, "confidence": None, "choice_probability": None,
                                 "reason_code": reason, "closed_h1_utc": None,
                                 "policy_version": POLICY_VERSION, "confirmed_for_trading": False,
                                 "observed_at": now_utc.astimezone(UTC).isoformat()})
