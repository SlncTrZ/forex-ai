"""Deterministic P2 weighted SHADOW research score, never a trading permission.

Input: closed-candle Signal Matrix + same-cycle, observational H1 Quant/D3.
Output: versioned, immutable research audit. No imports from execution/risk.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from forex_ai.journal.db import log_audit_event, session
from forex_ai.strategy.v1.contracts import fingerprint

POLICY = "shadow-weighted-v1"
EVENT = "SHADOW_SIGNAL_SCORE"
THRESHOLD = 75  # Research comparison threshold; NOT a broker admission gate.
WEIGHTS = {"setup": 28, "trend": 20, "momentum": 16, "volume": 14,
           "d3": 12, "news": 5, "history": 5}
assert sum(WEIGHTS.values()) == 100
_DIRECTIONS = {"BUY": "BULL", "SELL": "BEAR"}
_VALID_STATES = {"BULL", "BEAR", "NEUTRAL"}
_SETUP_KEYS = {"TRG", "SQZ", "ST", "STRUCT"}
_MAX_AGE = timedelta(minutes=16)


def _valid_regime(regime: Mapping[str, Any] | None, symbol: str, now: datetime) -> bool:
    """D3 *class* is advisory. Confidence is not calibrated P(win)."""
    if not isinstance(regime, Mapping) or regime.get("symbol") != symbol:
        return False
    if regime.get("market_state") != "ACTIVE":
        return False
    if regime.get("reason_code") not in {
        "REGIME_CONFIRMED", "REGIME_LOW_CONFIDENCE", "REGIME_LOW_CHOICE_PROBABILITY"
    }:
        return False
    if regime.get("model_regime") not in ("UPTREND", "DOWNTREND"):
        return False
    if regime.get("quant_regime") != regime.get("model_regime"):
        return False
    try:
        when = datetime.fromisoformat(str(regime["observed_at"])).astimezone(timezone.utc)
        closed = datetime.fromisoformat(str(regime["closed_h1_utc"])).astimezone(timezone.utc)
        conf, choice = regime["confidence"], regime["choice_probability"]
        return (0 <= (now - when).total_seconds() <= _MAX_AGE.total_seconds() and
                0 <= (now - closed).total_seconds() <= 3 * 3600 and
                type(conf) in (float, int) and math.isfinite(conf) and 0 <= conf <= 1 and
                type(choice) in (float, int) and math.isfinite(choice) and 0 <= choice <= 1)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def _component(name: str, observed: int, available: int, *, inputs: list[str]) -> dict:
    maximum = WEIGHTS[name]
    return {"id": name, "points": min(maximum, max(0, observed)), "max": maximum,
            "available": min(maximum, max(0, available)), "inputs": inputs[:8]}


def _scenario(row: Mapping[str, Any], direction: str,
              regime: Mapping[str, Any] | None, *, model_valid: bool) -> dict:
    desired = _DIRECTIONS[direction]
    cells = {str(cell.get("indicator")): cell for cell in row["cells"]
             if isinstance(cell, dict) and isinstance(cell.get("indicator"), str)}
    def state(indicator: str) -> str:
        value = cells.get(indicator, {}).get("state")
        return value if value in _VALID_STATES else "UNKNOWN"
    def aligned(indicator: str) -> bool:
        return state(indicator) == desired
    def available(indicator: str) -> bool:
        return state(indicator) in _VALID_STATES

    triggers = [key for key in ("ST", "SQZ", "STRUCT", "TRG")
                if key in cells and bool(cells[key].get("core"))]
    if not triggers:
        # A registry extension without trigger definitions must not earn setup points.
        triggers = []
    setup_match = sum(aligned(x) for x in triggers)
    setup_points = WEIGHTS["setup"] * setup_match // len(triggers) if triggers else 0
    setup_available = WEIGHTS["setup"] * sum(available(x) for x in triggers) // len(triggers) if triggers else 0
    ema = state("EMA")
    trend = _component("trend", 20 if ema == desired else 0,
                       20 if available("EMA") else 0, inputs=["EMA"])
    momentum = _component("momentum", sum(8 for key in ("RSI", "ADX") if aligned(key)),
                          sum(8 for key in ("RSI", "ADX") if available(key)),
                          inputs=["RSI", "ADX"])
    volume = _component("volume", 14 if aligned("VOL") else 0,
                        14 if available("VOL") else 0, inputs=["VOL"])
    expected = "UPTREND" if direction == "BUY" else "DOWNTREND"
    # Regime alone must never generate a scored directional opportunity when
    # every technical trigger is unavailable or none aligns with the side.
    d3_eligible = model_valid and setup_available == WEIGHTS["setup"] and setup_match > 0
    d3 = _component("d3", 12 if d3_eligible and regime.get("model_regime") == expected else 0,
                    12 if d3_eligible else 0, inputs=["H1 Quant/D3 agreement"])
    parts = [
        _component("setup", setup_points, setup_available, inputs=triggers),
        trend, momentum, volume, d3,
        _component("news", 0, 0, inputs=[]),
        _component("history", 0, 0, inputs=[]),
    ]
    score = sum(c["points"] for c in parts)
    possible = sum(c["available"] for c in parts)
    core_missing = bool(not triggers or any(not available(k) for k in triggers))
    return {
        "direction": direction, "score": score, "available_points": possible,
        "max": 100, "threshold": THRESHOLD,
        "setup_complete": bool(triggers and setup_match == len(triggers)),
        "missing_core": core_missing,
        "components": parts,
    }


def score_matrix(matrix: Mapping[str, Any], *,
                 regime: Mapping[str, Any] | None,
                 now_utc: datetime) -> dict:
    """Score an observation, never a candidate or broker order intent."""
    now = now_utc.astimezone(timezone.utc)
    symbol = str(matrix["symbol"])
    when = datetime.fromisoformat(str(matrix["observed_at"])).astimezone(timezone.utc)
    age = (now - when).total_seconds()
    active = (matrix.get("market_state") == "ACTIVE" and 0 <= age <= _MAX_AGE.total_seconds())
    d3_ok = active and _valid_regime(regime, symbol, now)
    results = []
    for row in matrix["rows"]:
        # Inactive, instrument-ineligible or off-session rows cannot earn points.
        enabled = row.get("enabled") is True
        eligible = row.get("eligible") is True
        usable = active and enabled and eligible
        scenarios = [_scenario(row, side, regime if d3_ok else None, model_valid=bool(d3_ok))
                     for side in ("BUY", "SELL")] if usable else []
        best = max(scenarios, key=lambda x: x["score"]) if scenarios else None
        if not active:
            verdict = "STALE"
        elif not eligible:
            verdict = "NOT_APPLICABLE"
        elif not enabled:
            verdict = "DISABLED"
        elif best and best["missing_core"]:
            verdict = "INSUFFICIENT_EVIDENCE"
        elif best and not best["setup_complete"]:
            verdict = "NO_COMPLETE_SETUP"
        elif best and best["score"] >= THRESHOLD:
            verdict = "SHADOW_THRESHOLD_MET"
        else:
            verdict = "BELOW_THRESHOLD"
        results.append({
            "strategy_id": row["strategy_id"], "symbol": symbol,
            "timeframe": row["timeframe"], "enabled": enabled,
            "eligible": eligible, "verdict": verdict,
            "best_direction": best["direction"] if best and best["score"] > 0 else None,
            "best_score": best["score"] if best else None,
            "scenarios": scenarios, "diagnostic_only": True,
        })
    result = {
        "policy_version": POLICY, "symbol": symbol, "observed_at": now.isoformat(),
        "source_matrix_fingerprint": str(matrix.get("fingerprint") or ""),
        "source_regime_fingerprint": str(regime.get("feature_fingerprint") or "") if d3_ok else "",
        "market_state": matrix.get("market_state"), "threshold": THRESHOLD,
        "weights": dict(WEIGHTS), "d3_observation_valid": bool(d3_ok),
        "results": results, "diagnostic_only": True,
    }
    result["fingerprint"] = fingerprint(result)
    return result


def observe_score(db_path, result: dict) -> dict:
    """Idempotent audit; one write per scored matrix/Regime observation."""
    with session(db_path) as con:
        previous = con.execute(
            "SELECT 1 FROM audit_events WHERE event_type=? AND entity_id=? LIMIT 1",
            (EVENT, result["fingerprint"]),
        ).fetchone()
    if previous is None:
        log_audit_event(
            db_path, event_type=EVENT, source="shadow_weighted_observer",
            symbol=result["symbol"], entity_id=result["fingerprint"], payload=result,
        )
    return result
