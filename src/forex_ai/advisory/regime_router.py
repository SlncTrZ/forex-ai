"""Active H1 market-regime gate for Forex-AI candidate admission.

Uses only completed H1 bars, deterministic ADX/DMI and EMA features, and the
existing authenticated D3 scoring transport. A D3 disagreement/timeout or
missing evidence produces UNCERTAIN and vetoes new candidates. No broker order
is ever sent here. Cached results are bound to features, candle and model.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from forex_ai.advisory.ollama_safety_gate import D3DecisionSafetyGate, GateUnavailable
from forex_ai.journal.db import log_audit_event, session
from forex_ai.market.indicators import atr, bollinger_bands, dmi_adx, ema
from forex_ai.strategy.v1.contracts import MarketSnapshot, fingerprint

UTC = timezone.utc
REGIMES = ("UPTREND", "DOWNTREND", "SIDEWAY")
POLICY_VERSION = "regime-h1-v1"
MIN_H1_BARS = 220
REGIME_CONFIDENCE_FLOOR = 0.75


@dataclass(frozen=True)
class RegimeFeatures:
    symbol: str
    closed_h1_utc: str
    ema_fast: float
    ema_slow: float
    ema_slope_atr: float
    ema_gap_atr: float
    adx: float
    plus_di: float
    minus_di: float
    bb_width_atr: float
    atr_h1: float
    volume_ratio: float
    quant_regime: str
    fingerprint: str


@dataclass(frozen=True)
class RegimeDecision:
    symbol: str
    regime: str
    quant_regime: str
    model_regime: str | None
    confidence: float | None
    reason_code: str
    closed_h1_utc: str | None
    feature_fingerprint: str
    model_fingerprint: str
    latency_ms: int
    policy_version: str = POLICY_VERSION
    atr_h1: float | None = None

    def to_audit(self) -> dict[str, Any]:
        return asdict(self)


def quant_features(market: MarketSnapshot) -> RegimeFeatures | None:
    """Use completed H1 bars only; MT5 timestamps designate bar OPEN time."""
    tf = market.timeframes.get("H1")
    if tf is None:
        return None
    asof = market.captured_at_utc.astimezone(UTC)
    bars = [
        b for b in tf.closed_bars
        if b.time_utc.astimezone(UTC) + timedelta(hours=1) <= asof
    ]
    if len(bars) < MIN_H1_BARS:
        return None
    bars = bars[-300:]
    if asof - (bars[-1].time_utc.astimezone(UTC) + timedelta(hours=1)) > timedelta(hours=3):
        return None
    dmi = dmi_adx(bars, 14)
    a = atr(bars, 14)
    if dmi is None or not math.isfinite(a) or a <= 0:
        return None
    closes = [b.close for b in bars]
    fast = ema(closes, 50)
    slow = ema(closes, 200)
    prior = ema(closes[:-4], 50)
    slope = (fast - prior) / a
    gap = (fast - slow) / a
    bb = bollinger_bands(closes, 20, 2.0)
    width = (bb.upper - bb.lower) / a if bb else float("inf")
    avg_volume = sum(b.volume for b in bars[-21:-1]) / 20
    volume = bars[-1].volume / avg_volume if avg_volume > 0 else 0.0
    adx = dmi.adx
    if (adx >= 22 and gap >= 0.3 and slope >= 0.08
            and dmi.plus_di > dmi.minus_di):
        quant = "UPTREND"
    elif (adx >= 22 and gap <= -0.3 and slope <= -0.08
            and dmi.minus_di > dmi.plus_di):
        quant = "DOWNTREND"
    elif (adx < 20 and abs(slope) <= 0.22
            and (abs(gap) <= 1.5 or width <= 2.0)):
        quant = "SIDEWAY"
    else:
        quant = "UNCERTAIN"
    closed = (bars[-1].time_utc.astimezone(UTC) + timedelta(hours=1)).isoformat()
    evidence = {
        "symbol": market.symbol, "closed_h1_utc": closed,
        "ema_fast": round(fast, 8), "ema_slow": round(slow, 8),
        "ema_slope_atr": round(slope, 5), "ema_gap_atr": round(gap, 5),
        "adx": round(adx, 4), "plus_di": round(dmi.plus_di, 4),
        "minus_di": round(dmi.minus_di, 4),
        "bb_width_atr": round(width, 5), "atr_h1": round(a, 8),
        "volume_ratio": round(volume, 5), "quant_regime": quant,
    }
    if not all(math.isfinite(v) for v in evidence.values() if isinstance(v, (float, int))):
        return None
    return RegimeFeatures(**evidence, fingerprint=fingerprint({"version": POLICY_VERSION, **evidence}))


def _model_choice(document: object) -> tuple[str, float]:
    """Fail closed unless D3 supplies a coherent structured 3-way choice."""
    if not isinstance(document, dict):
        raise GateUnavailable("REGIME_INVALID_RESPONSE")
    candidates = [document]
    for k in ("answers", "results", "decisions", "data"):
        child = document.get(k)
        if isinstance(child, dict):
            candidates.append(child)
            for nested in ("answers", "results"):
                if isinstance(child.get(nested), dict):
                    candidates.append(child[nested])
    for container in candidates:
        item = container.get("regime")
        if not isinstance(item, dict) or not isinstance(item.get("choice"), str):
            continue
        choice = item["choice"]
        probs = item.get("probabilities")
        confidence = item.get("confidence")
        if choice not in REGIMES or not isinstance(probs, dict):
            raise GateUnavailable("REGIME_INVALID_CHOICE")
        if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise GateUnavailable("REGIME_INVALID_CONFIDENCE")
        probabilities = [probs.get(k) for k in REGIMES]
        if any(type(x) not in (float, int) or not math.isfinite(x) or not 0 <= x <= 1 for x in probabilities):
            raise GateUnavailable("REGIME_INVALID_PROBABILITIES")
        if abs(sum(probabilities)-1) > 0.05:
            raise GateUnavailable("REGIME_PROBABILITY_MISMATCH")
        chosen = probs[choice]
        if chosen + 1e-6 < max(probabilities) or abs(chosen-confidence) > 0.20:
            raise GateUnavailable("REGIME_CHOICE_CONFLICT")
        return choice, float(min(chosen, confidence))
    raise GateUnavailable("REGIME_CHOICE_MISSING")


def route_candidate(regime: RegimeDecision, *, strategy_id: str, side: str,
                    evidence_codes: tuple[str, ...] = ()) -> str | None:
    """Return a veto reason or None; never elevates a risk limit."""
    if regime.regime not in REGIMES:
        return "REGIME_UNCERTAIN_VETO"
    if side not in ("BUY", "SELL"):
        return "REGIME_INVALID_SIDE"
    if regime.regime == "UPTREND" and side != "BUY":
        return "REGIME_DIRECTION_VETO"
    if regime.regime == "DOWNTREND" and side != "SELL":
        return "REGIME_DIRECTION_VETO"
    if regime.regime == "SIDEWAY" and not (
        strategy_id == "btc_squeeze_breakout_m15"
        and "BTC_SQUEEZE_BREAKOUT_CONFIRMED" in evidence_codes
    ):
        return "REGIME_SIDEWAY_VETO"
    # Exception requires source-backed squeeze-fire evidence, not merely an ID.
    return None


def _decision(features: RegimeFeatures | None, symbol: str, provider: D3DecisionSafetyGate | None) -> RegimeDecision:
    model_fp = provider.fingerprint if provider else "D3_UNCONFIGURED"
    if features is None:
        return RegimeDecision(symbol, "UNCERTAIN", "UNCERTAIN", None, None,
                              "REGIME_H1_DATA_UNAVAILABLE", None, "", model_fp, 0)
    base = dict(symbol=symbol, regime="UNCERTAIN", quant_regime=features.quant_regime,
                model_regime=None, confidence=None, closed_h1_utc=features.closed_h1_utc,
                feature_fingerprint=features.fingerprint, model_fingerprint=model_fp,
                latency_ms=0, atr_h1=features.atr_h1)
    if features.quant_regime == "UNCERTAIN":
        return RegimeDecision(**base, reason_code="REGIME_QUANT_UNCERTAIN")
    if provider is None:
        return RegimeDecision(**base, reason_code="REGIME_D3_UNAVAILABLE")
    # The model sees measured features only, not the Quant label. This
    # prevents answer leakage that would inflate apparent model agreement.
    payload = {k: v for k, v in asdict(features).items()
               if k not in ("fingerprint", "quant_regime")}
    import time
    start = time.monotonic()
    try:
        result = provider.decide_questions(
            state=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            questions={"regime": {
                "type": "choice",
                "instructions": (
                    "Classify ONLY these closed-H1 observed market features. "
                    "UPTREND requires strong positive directional evidence; "
                    "DOWNTREND requires strong negative directional evidence; "
                    "SIDEWAY requires low ADX plus compression/flat structure. "
                    "Do not infer missing news or future bars."
                ),
                "criteria": {
                    "UPTREND": "Evidence supports sustained positive H1 trend",
                    "DOWNTREND": "Evidence supports sustained negative H1 trend",
                    "SIDEWAY": "Evidence supports confirmed range/compression rather than a directional trend",
                },
            }},
        )
        model_regime, confidence = _model_choice(result)
        elapsed = int((time.monotonic() - start) * 1000)
        if confidence < REGIME_CONFIDENCE_FLOOR:
            reason = "REGIME_LOW_CONFIDENCE"
        elif model_regime != features.quant_regime:
            reason = "REGIME_MODEL_QUANT_CONFLICT"
        else:
            return RegimeDecision(**{**base, "regime": model_regime, "model_regime": model_regime,
                                     "confidence": confidence, "latency_ms": elapsed},
                                  reason_code="REGIME_CONFIRMED")
        return RegimeDecision(**{**base, "model_regime": model_regime,
                                 "confidence": confidence, "latency_ms": elapsed},
                              reason_code=reason)
    except (GateUnavailable, ValueError, TypeError):
        return RegimeDecision(**{**base, "latency_ms": int((time.monotonic() - start) * 1000)},
                              reason_code="REGIME_D3_UNAVAILABLE")


def resolve_regime(market: MarketSnapshot, *, db_path, provider: D3DecisionSafetyGate | None) -> RegimeDecision:
    """Audit/cache one decision per closed H1 feature+model fingerprint."""
    features = quant_features(market)
    fp = features.fingerprint if features else fingerprint({
        "symbol": market.symbol, "captured_h1_hour": market.captured_at_utc.astimezone(UTC).strftime("%Y-%m-%dT%H")
    })
    model_fp = provider.fingerprint if provider else "D3_UNCONFIGURED"
    key = hashlib.sha256(f"{POLICY_VERSION}:{market.symbol}:{fp}:{model_fp}".encode()).hexdigest()
    with session(db_path) as con:
        row = con.execute(
            "SELECT payload_json FROM audit_events WHERE event_type='D3_REGIME_DECISION' "
            "AND entity_id=? ORDER BY id DESC LIMIT 1", (key,),
        ).fetchone()
    if row:
        try:
            data = json.loads(row["payload_json"])
            return RegimeDecision(**{k: data[k] for k in RegimeDecision.__dataclass_fields__
                                     if k in data})
        except (ValueError, TypeError, KeyError):
            pass
    decision = _decision(features, market.symbol, provider)
    log_audit_event(db_path, event_type="D3_REGIME_DECISION",
                    source="production_v1_scanner", symbol=market.symbol,
                    entity_id=key, payload=decision.to_audit())
    return decision
