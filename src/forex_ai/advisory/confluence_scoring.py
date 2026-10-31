"""D3-backed, evidence-capped confluence scoring; never a trading executor.

The D3 ordinal scores suggest quality but cannot manufacture source evidence.
Deterministic caps constrain each score, thresholds may only REDUCE risk,
and unavailable/malformed model output always VETOs candidate admission.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from forex_ai.advisory.ollama_safety_gate import D3DecisionSafetyGate, GateUnavailable
from forex_ai.advisory.regime_router import RegimeDecision, REGIMES, route_candidate
from forex_ai.journal.db import log_audit_event, session
from forex_ai.strategy.v1.contracts import StrategyResult, fingerprint

POLICY_VERSION = "confluence-v1"
WEIGHTS = {"alignment": 30, "value": 25, "momentum": 25, "trigger": 20}
SIGNAL_CONFIRMATIONS = frozenset({
    "SMC_PRO_FVG_CONFIRMED",
    "KEYLEVEL_SIGNAL_CONFIRMED",
    "SUPERTREND_VOLUME_CONFIRMED",
    "BTC_SQUEEZE_BREAKOUT_CONFIRMED",
    "SUPERTREND_STOCHASTIC_CONFIRMED",
    "MTF_TREND_CONTINUATION_CONFIRMED",
    "PRO_VOLATILITY_EXPANSION_CONFIRMED",
    "ICHIMOKU_MTF_CONFIRMED",
})
VALUE_FIELDS = ("st_value", "fvg_ce", "sweep_price", "resistance", "support")


@dataclass(frozen=True)
class ConfluenceDecision:
    candidate_id: str
    strategy_id: str
    symbol: str
    regime: str
    score: int
    alignment_points: int
    value_points: int
    momentum_points: int
    trigger_points: int
    risk_multiplier: str
    verdict: str
    reason_code: str
    model_fingerprint: str
    input_fingerprint: str
    latency_ms: int
    policy_version: str = POLICY_VERSION

    @property
    def allowed(self) -> bool:
        return self.verdict in ("FULL", "REDUCE")

    @property
    def multiplier(self) -> Decimal:
        return Decimal(self.risk_multiplier)


def _finite(v: Any) -> float | None:
    if type(v) not in (float, int, Decimal):
        return None
    try:
        x = float(v)
    except (ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _d3_score(answer: object) -> float:
    """Validate a three-category score as E[ordinal 0..2]."""
    if not isinstance(answer, dict) or answer.get("type") != "score":
        raise GateUnavailable("CONFLUENCE_INVALID_SCORE_TYPE")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        raise GateUnavailable("CONFLUENCE_SCORE_PROBABILITIES_MISSING")
    vals = [_finite(probabilities.get(str(i))) for i in range(3)]
    score = _finite(answer.get("score"))
    confidence = _finite(answer.get("confidence"))
    if (score is None or confidence is None or not 0 <= confidence <= 1 or
            any(x is None or not 0 <= x <= 1 for x in vals)):
        raise GateUnavailable("CONFLUENCE_SCORE_MALFORMED")
    if abs(sum(vals) - 1) > 0.05 or not 0 <= score <= 2:
        raise GateUnavailable("CONFLUENCE_SCORE_RANGE")
    expected = vals[1] + 2 * vals[2]
    if abs(score - expected) > 0.10:
        raise GateUnavailable("CONFLUENCE_SCORE_INCONSISTENT")
    return min(1.0, max(0.0, score / 2))


def _observations(result: StrategyResult, regime: RegimeDecision) -> dict:
    candidate = result.candidate
    if candidate is None:
        raise ValueError("confluence requires candidate")
    values = result.evidence.values
    entry = _finite(candidate.reference_entry)
    if entry is None or entry <= 0:
        raise ValueError("invalid candidate entry")
    # Do not use target/stop distances as proxies for value or momentum.
    refs = {k: _finite(values.get(k)) for k in VALUE_FIELDS}
    refs = {k: v for k, v in refs.items() if v is not None and v > 0}
    atr_h1 = _finite(regime.atr_h1)
    # Only the upstream closed-H1 regime feature supplies ATR; strategy
    # self-reported ATR cannot silently replace this audited reference.
    distances = {k: abs(entry - v) for k, v in refs.items()}
    vol_ratio = _finite(values.get("vol_ratio"))
    if vol_ratio is not None and vol_ratio < 0:
        vol_ratio = None
    confirmed = sorted(set(result.evidence.reason_codes) & SIGNAL_CONFIRMATIONS)
    source = {
        "candidate_id": candidate.candidate_id,
        "strategy_id": candidate.strategy_id,
        "symbol": candidate.symbol,
        "side": candidate.side,
        "regime": regime.regime,
        "closed_h1_utc": regime.closed_h1_utc,
        "regime_feature_fingerprint": regime.feature_fingerprint,
        "regime_model_fingerprint": regime.model_fingerprint,
        "value_source_fields": sorted(refs),
        "nearest_value_distance": min(distances.values()) if distances else None,
        "atr_h1": atr_h1,
        "volume_ratio": vol_ratio,
        "confirmed_trigger_codes": confirmed,
        "evidence_fingerprint": result.evidence.evidence_hash,
    }
    # Without a verified distance normalized to volatility, value is capped
    # to half-points; models cannot infer a precise "sweet spot".
    value_cap = 0
    if distances:
        value_cap = 12
        if atr_h1 is not None and atr_h1 > 0:
            normalized = min(distances.values()) / atr_h1
            if normalized <= 1.5:
                value_cap = 25
            elif normalized > 3:
                value_cap = 0
    momentum_cap = 25 if vol_ratio is not None and vol_ratio >= 1.15 else (
        15 if vol_ratio is not None and vol_ratio >= 1 else 0)
    trigger_cap = 20 if confirmed else 0
    side_ok = route_candidate(
        regime, strategy_id=candidate.strategy_id, side=candidate.side,
        evidence_codes=result.evidence.reason_codes,
    ) is None
    alignment_cap = 30 if side_ok and regime.regime in REGIMES else 0
    source["caps"] = {
        "alignment": alignment_cap,
        "value": value_cap,
        "momentum": momentum_cap,
        "trigger": trigger_cap,
    }
    return source


def _block(result: StrategyResult, regime: RegimeDecision, reason: str, fp: str,
           model_fp: str, latency_ms: int = 0) -> ConfluenceDecision:
    candidate = result.candidate
    assert candidate is not None
    return ConfluenceDecision(
        candidate.candidate_id, candidate.strategy_id, candidate.symbol,
        regime.regime, 0, 0, 0, 0, 0, "0", "VETO", reason,
        model_fp, fp, latency_ms,
    )


def assess_confluence(result: StrategyResult, regime: RegimeDecision,
                      *, db_path, provider: D3DecisionSafetyGate | None) -> ConfluenceDecision:
    """Effective decision; cache by candidate/quant evidence/model/policy."""
    candidate = result.candidate
    if candidate is None:
        raise ValueError("candidate required")
    model_fp = provider.fingerprint if provider is not None else "D3_UNCONFIGURED"
    try:
        source = _observations(result, regime)
    except ValueError:
        source = {"candidate_id": candidate.candidate_id, "invalid": True}
    fp = fingerprint({"source": source, "policy_version": POLICY_VERSION})
    key = hashlib.sha256(f"{candidate.candidate_id}:{fp}:{model_fp}".encode()).hexdigest()
    with session(db_path) as con:
        row = con.execute(
            "SELECT payload_json FROM audit_events WHERE event_type='D3_CONFLUENCE_DECISION' "
            "AND entity_id=? ORDER BY id DESC LIMIT 1", (key,)
        ).fetchone()
    if row is not None:
        try:
            data = json.loads(row["payload_json"])
            cached = ConfluenceDecision(**data)
            if (cached.input_fingerprint == fp and cached.model_fingerprint == model_fp
                    and cached.policy_version == POLICY_VERSION):
                return cached
        except (ValueError, TypeError, KeyError):
            pass
    start = time.monotonic()
    reason = None
    if source.get("invalid"):
        reason = "CONFLUENCE_INVALID_EVIDENCE"
    elif source["caps"]["alignment"] == 0:
        reason = "CONFLUENCE_REGIME_VETO"
    elif provider is None:
        reason = "CONFLUENCE_D3_UNAVAILABLE"
    if reason is not None:
        decision = _block(result, regime, reason, fp, model_fp)
    else:
        # Only preverified features; no broker identities, tokens, news guesses.
        questions = {
            name: {
                "type": "score",
                "instructions": (
                    "Grade ONLY the evidence in the supplied candidate feature state. "
                    "0 weak/no demonstrated evidence, 1 mixed, 2 strong; "
                    "never infer unprovided signals. Risk score is NOT P(win)."
                ),
                "criteria": ["weak_or_missing", "moderate_verified", "strong_verified"],
            }
            for name in ("value", "momentum", "trigger")
        }
        try:
            document = provider.decide_questions(
                state=json.dumps(source, sort_keys=True, separators=(",", ":")),
                questions=questions,
            )
            answers = document.get("answers") if isinstance(document, dict) else None
            if not isinstance(answers, dict):
                raise GateUnavailable("CONFLUENCE_ANSWERS_MISSING")
            scores = {name: _d3_score(answers.get(name)) for name in questions}
            caps = source["caps"]
            alignment = caps["alignment"]
            value = int(round(caps["value"] * scores["value"]))
            momentum = int(round(caps["momentum"] * scores["momentum"]))
            trigger = int(round(caps["trigger"] * scores["trigger"]))
            total = min(100, alignment + value + momentum + trigger)
            verdict, multiplier, reason = (
                ("FULL", "1", "CONFLUENCE_FULL") if total >= 80 else
                ("REDUCE", "0.5", "CONFLUENCE_REDUCED") if total >= 65 else
                ("VETO", "0", "CONFLUENCE_BELOW_THRESHOLD")
            )
            decision = ConfluenceDecision(
                candidate.candidate_id, candidate.strategy_id, candidate.symbol,
                regime.regime, total, alignment, value, momentum, trigger,
                multiplier, verdict, reason, model_fp, fp,
                int((time.monotonic() - start) * 1000),
            )
        except (GateUnavailable, ValueError, TypeError, OverflowError):
            decision = _block(result, regime, "CONFLUENCE_D3_UNAVAILABLE", fp, model_fp,
                              int((time.monotonic() - start) * 1000))
    log_audit_event(
        db_path, event_type="D3_CONFLUENCE_DECISION",
        source="production_v1_scanner", symbol=candidate.symbol,
        entity_id=key, correlation_id=candidate.correlation_id,
        payload=asdict(decision),
    )
    return decision
