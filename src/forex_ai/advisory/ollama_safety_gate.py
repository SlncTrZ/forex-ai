"""D3 decision-model pre-execution safety filter (zero-generation protocol).

The service classifies a sanitized order context through a remote decision API.
The result never creates, increases, modifies or sends an order. The caller
owns the deterministic fresh risk, broker preflight and trading-control checks.
All failures are fail-closed. No raw model response or credential is logged.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import httpx

from forex_ai.execution.state import OrderIntent

UTC = timezone.utc
VERSION = "d3-pre-execution-v1"
DECISION_PATH = "/api/v1/decisions"
EXPECTED_MODEL = "d3:4b"
MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


class GateUnavailable(RuntimeError):
    """Unavailable or unusable D3 response. Must block a new entry."""


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    reason_code: str
    model_fingerprint: str
    latency_ms: int
    request_digest: str


def _decision_url(endpoint: str) -> str:
    """Accept only a private HTTP IP or HTTPS origin, no inline credentials."""
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise GateUnavailable("D3_INVALID_ENDPOINT") from exc
    if (
        not host or parsed.scheme not in ("http", "https")
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.path not in ("", "/", DECISION_PATH)
    ):
        raise GateUnavailable("D3_INVALID_ENDPOINT")
    if parsed.scheme == "http":
        try:
            addr = ipaddress.ip_address(host)
        except ValueError as exc:
            raise GateUnavailable("D3_HTTP_REQUIRES_PRIVATE_IP") from exc
        if not (addr.is_private and not addr.is_loopback and not addr.is_link_local
                and not addr.is_multicast and not addr.is_unspecified):
            raise GateUnavailable("D3_HTTP_ENDPOINT_NOT_PRIVATE")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin + DECISION_PATH


def _snapshot(intent: OrderIntent, request: dict, context: dict | None) -> dict:
    """Strict field allowlist: no account login, broker server or raw payload."""
    fields = {
        "intent_id": intent.intent_id,
        "candidate_id": intent.candidate_id,
        "symbol": intent.symbol,
        "side": intent.side,
        "volume": str(intent.volume),
        "entry": str(intent.entry),
        "stop_loss": str(intent.stop_loss),
        "take_profit": str(intent.take_profit),
        "age_seconds": max(0, round((datetime.now(UTC) -
            intent.created_at_utc.astimezone(UTC)).total_seconds(), 2)),
    }
    safe_request = {}
    for key in ("action", "symbol", "type", "volume", "price", "sl", "tp"):
        value = request.get(key)
        if type(value) in (str, int, float, Decimal):
            safe_request[key] = str(value)[:60]
    fields["order_request"] = safe_request
    safe_market = {}
    for key in ("strategy_id", "account_trade_mode", "tick_age_ms", "bid", "ask",
                "spread_points", "stop_distance_points", "target_distance_points"):
        value = (context or {}).get(key)
        if type(value) in (str, int, float):
            safe_market[key] = str(value)[:75]
    fields["market"] = safe_market
    # Do not pretend the model has external macro, SMC or volume evidence.
    fields["unavailable_features"] = [
        "verified_red_news", "verified_smc_structure", "verified_volume_context",
        "verified_multi_timeframe_trend",
    ]
    return fields


def _deterministic_block(intent: OrderIntent, request: dict) -> str | None:
    """Non-negotiable pre-model protections; AI ALLOW can never override these."""
    try:
        entry, stop, target, volume = (
            Decimal(str(intent.entry)), Decimal(str(intent.stop_loss)),
            Decimal(str(intent.take_profit)), Decimal(str(intent.volume)),
        )
        if not all(v.is_finite() and v > 0 for v in (entry, stop, target, volume)):
            return "D3_INVALID_ORDER_VALUES"
        if intent.side == "BUY":
            if not stop < entry < target:
                return "D3_BUY_PROTECTION_CONFLICT"
        elif intent.side == "SELL":
            if not target < entry < stop:
                return "D3_SELL_PROTECTION_CONFLICT"
        else:
            return "D3_INVALID_ORDER_SIDE"
        if request.get("symbol") not in (None, intent.symbol):
            return "D3_ORDER_SYMBOL_MISMATCH"
        for field, intended in (("volume", volume), ("sl", stop), ("tp", target)):
            if field in request and Decimal(str(request[field])) != intended:
                return "D3_ORDER_REQUEST_MISMATCH"
    except (ArithmeticError, ValueError, TypeError):
        return "D3_INVALID_ORDER_VALUES"
    return None


def _answer(document: object) -> dict:
    """D3 responses may wrap answers in a result collection; never infer a choice."""
    if not isinstance(document, dict):
        raise GateUnavailable("D3_INVALID_RESPONSE")
    candidates = [document]
    for name in ("results", "answers", "decisions", "questions", "data"):
        child = document.get(name)
        if isinstance(child, dict):
            candidates.append(child)
            for inner in ("results", "answers", "decisions"):
                if isinstance(child.get(inner), dict):
                    candidates.append(child[inner])
    for item in candidates:
        action = item.get("action")
        if isinstance(action, dict) and isinstance(action.get("choice"), str):
            return action
    raise GateUnavailable("D3_ACTION_MISSING")


def _unit(value: object) -> float | None:
    if type(value) not in (float, int):
        return None
    f = float(value)
    return f if math.isfinite(f) and 0 <= f <= 1 else None


def _verdict(document: object) -> tuple[bool, str]:
    action = _answer(document)
    choice = action["choice"]
    if choice not in ("execute", "reject"):
        raise GateUnavailable("D3_UNKNOWN_CHOICE")
    confidence = _unit(action.get("confidence"))
    probabilities = action.get("probabilities")
    if not isinstance(probabilities, dict):
        raise GateUnavailable("D3_PROBABILITIES_MISSING")
    p_execute = _unit(probabilities.get("execute"))
    p_reject = _unit(probabilities.get("reject"))
    if None in (confidence, p_execute, p_reject):
        raise GateUnavailable("D3_INVALID_PROBABILITIES")
    if abs(p_execute + p_reject - 1) > 0.05:
        raise GateUnavailable("D3_PROBABILITIES_INCONSISTENT")
    if choice == "reject":
        return False, "D3_MODEL_REJECT"
    # A model ALLOW recommendation cannot elevate a weak/ambiguous score.
    if confidence < 0.8 or p_execute < 0.8 or p_execute < p_reject:
        return False, "D3_UNCERTAIN_EXECUTE"
    return True, "D3_EXECUTE_PASS"


class D3DecisionSafetyGate:
    """Mandatory classifier for a prepared new-entry intent, not a trade executor."""

    def __init__(self, *, endpoint: str, model: str = EXPECTED_MODEL,
                 api_key: str | None = None, timeout_seconds: float = 8):
        self.endpoint = _decision_url(endpoint)
        if not MODEL_RE.fullmatch(model):
            raise GateUnavailable("D3_INVALID_MODEL")
        if not 1 <= timeout_seconds <= 20:
            raise GateUnavailable("D3_INVALID_TIMEOUT")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._api_key = api_key if api_key is not None else (
            os.getenv("DECISION_MODEL_API_KEY") or os.getenv("OLLAMA_KEY", "")
        )
        if not self._api_key or any(ch in self._api_key for ch in "\r\n"):
            raise GateUnavailable("D3_AUTH_NOT_CONFIGURED")
        self.fingerprint = hashlib.sha256(
            f"{VERSION}:{self.model}".encode()
        ).hexdigest()[:16]

    @classmethod
    def from_environment(cls) -> "D3DecisionSafetyGate":
        endpoint = (
            os.getenv("DECISION_MODEL_URL")
            or os.getenv("FOREX_AI_SAFETY_GATE_URL", "")
        ).strip()
        if not endpoint:
            raise GateUnavailable("D3_ENDPOINT_NOT_CONFIGURED")
        model = os.getenv("FOREX_AI_SAFETY_GATE_MODEL", EXPECTED_MODEL).strip()
        try:
            timeout = float(os.getenv("FOREX_AI_SAFETY_GATE_TIMEOUT_SECONDS", "8"))
        except ValueError as exc:
            raise GateUnavailable("D3_INVALID_TIMEOUT") from exc
        return cls(endpoint=endpoint, model=model, timeout_seconds=timeout)

    def decide_questions(self, *, state: str, questions: dict) -> dict:
        """Existing authenticated D3 transport for regime/score classification.

        Does not send broker orders. Callers validate the exact answer schema.
        No secrets, URLs or raw responses are logged.
        """
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(2, self.timeout_seconds)),
                follow_redirects=False, trust_env=False,
            ) as client:
                response = client.post(
                    self.endpoint,
                    json={"model": self.model, "state": state, "questions": questions},
                    headers={"Authorization": "Bearer " + self._api_key,
                             "Content-Type": "application/json"},
                )
                response.raise_for_status()
                if len(response.content) > 65536:
                    raise GateUnavailable("D3_RESPONSE_TOO_LARGE")
                result = response.json()
                if not isinstance(result, dict):
                    raise GateUnavailable("D3_INVALID_RESPONSE")
                return result
        except GateUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise GateUnavailable("D3_PROVIDER_UNAVAILABLE") from exc

    def __call__(self, intent: OrderIntent, request: dict,
                 *, context: dict | None = None) -> SafetyVerdict:
        state = _snapshot(intent, request, context)
        raw_state = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw_state.encode()).hexdigest()
        violation = _deterministic_block(intent, request)
        if violation:
            # A local hard block is effective on the real send path even if the
            # remote model would incorrectly say execute.
            return SafetyVerdict(
                allowed=False, reason_code=violation,
                model_fingerprint=self.fingerprint, latency_ms=0,
                request_digest=digest,
            )
        payload = {
            "model": self.model,
            "state": raw_state,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": (
                        "Pre-execution risk classification on supplied verified facts only. "
                        "Choose reject for inconsistent BUY/SELL protection, invalid values, "
                        "stale tick, excessive spread, or a known material conflict. "
                        "Choose execute only if no material blocker is evident. "
                        "Do not infer news, trend, SMC or volume that were not supplied. "
                        "This is advisory, never permission to bypass broker risk checks."
                    ),
                    "criteria": {
                        "execute": "No identified material safety conflict in provided facts",
                        "reject": "Detected material safety conflict or unacceptable uncertainty",
                    },
                },
                "safety_score": {
                    "type": "score",
                    "instructions": "Grade only the safety evidenced by supplied facts.",
                    "criteria": ["Danger", "Caution", "Safe"],
                },
            },
        }
        start = time.monotonic()
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(2, self.timeout_seconds)),
                follow_redirects=False, trust_env=False,
            ) as client:
                response = client.post(
                    self.endpoint, json=payload,
                    headers={"Authorization": "Bearer " + self._api_key,
                             "Content-Type": "application/json"},
                )
                response.raise_for_status()
                if len(response.content) > 65536:
                    raise GateUnavailable("D3_RESPONSE_TOO_LARGE")
                data = response.json()
            allowed, reason = _verdict(data)
            return SafetyVerdict(
                allowed=allowed, reason_code=reason,
                model_fingerprint=self.fingerprint,
                latency_ms=int((time.monotonic() - start) * 1000),
                request_digest=digest,
            )
        except GateUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            # Do not log exception text: HTTP details may include auth headers.
            raise GateUnavailable("D3_PROVIDER_UNAVAILABLE") from exc


# Compatibility with the existing real execution callback; no /api/chat path.
OllamaSafetyGate = D3DecisionSafetyGate
