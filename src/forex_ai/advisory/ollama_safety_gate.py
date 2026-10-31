"""Required, fail-closed pre-execution AI Safety Gate for MT5 new-entry intents.

No market orders, credentials or open positions are sent to the model. The
model can only recommend ALLOW/BLOCK. All deterministic account, fresh risk,
broker order_check and Kill Switch guards remain authoritative.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from forex_ai.execution.state import OrderIntent

REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,55}$")
UTC = timezone.utc


class GateUnavailable(RuntimeError):
    """The model cannot safely decide. Never disclose transport details."""


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    reason_code: str
    model_fingerprint: str
    latency_ms: int
    request_digest: str


class _GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent_id: str = Field(min_length=5, max_length=120)
    request_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision: str = Field(pattern=r"^(ALLOW|BLOCK)$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,55}$")


def _private_server(url: str) -> str:
    """Require authenticated TLS hostname or unambiguous private IP; no public HTTP."""
    raw = urlsplit(url)
    if raw.username or raw.password or raw.query or raw.fragment or raw.path not in ("", "/"):
        raise GateUnavailable("AI_GATE_INVALID_ENDPOINT")
    host = raw.hostname
    if not host or raw.scheme not in ("http", "https"):
        raise GateUnavailable("AI_GATE_INVALID_ENDPOINT")
    if raw.scheme == "http":
        try:
            ip = ipaddress.ip_address(host)
        except ValueError as exc:
            raise GateUnavailable("AI_GATE_HTTP_REQUIRES_PRIVATE_IP") from exc
        if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise GateUnavailable("AI_GATE_ENDPOINT_NOT_PRIVATE")
    if raw.port is not None and (raw.port < 1 or raw.port > 65535):
        raise GateUnavailable("AI_GATE_INVALID_PORT")
    return url.rstrip("/")


def _safety_payload(intent: OrderIntent, *, now_utc: datetime) -> dict:
    # Explicit allowlist prevents leaking account IDs, broker login, env or keys.
    return {
        "intent_id": intent.intent_id,
        "candidate_id": intent.candidate_id,
        "symbol": intent.symbol,
        "side": intent.side,
        "volume": str(intent.volume),
        "entry": str(intent.entry),
        "stop_loss": str(intent.stop_loss),
        "take_profit": str(intent.take_profit),
        "intent_created_at_utc": intent.created_at_utc.astimezone(UTC).isoformat(),
        "decision_at_utc": now_utc.astimezone(UTC).isoformat(),
    }


class OllamaSafetyGate:
    """Calls /api/chat with strict structured output, validates intent digest."""

    def __init__(self, *, endpoint: str, model: str, timeout_seconds: float = 8):
        self.endpoint = _private_server(endpoint)
        self.model = model.strip()
        if not self.model or len(self.model) > 160 or not re.fullmatch(r"[A-Za-z0-9._:/-]+", self.model):
            raise GateUnavailable("AI_GATE_INVALID_MODEL")
        if not 1 <= timeout_seconds <= 20:
            raise GateUnavailable("AI_GATE_INVALID_TIMEOUT")
        self.timeout_seconds = timeout_seconds
        self.fingerprint = hashlib.sha256(("ollama-safety-v1:" + self.model).encode()).hexdigest()[:16]

    @classmethod
    def from_environment(cls) -> "OllamaSafetyGate":
        endpoint = os.getenv("FOREX_AI_SAFETY_GATE_URL", "").strip()
        model = os.getenv("FOREX_AI_SAFETY_GATE_MODEL", "").strip()
        if not endpoint or not model:
            raise GateUnavailable("AI_GATE_NOT_CONFIGURED")
        try:
            timeout = float(os.getenv("FOREX_AI_SAFETY_GATE_TIMEOUT_SECONDS", "8"))
        except ValueError as exc:
            raise GateUnavailable("AI_GATE_INVALID_TIMEOUT") from exc
        return cls(endpoint=endpoint, model=model, timeout_seconds=timeout)

    def __call__(self, intent: OrderIntent, request: dict, *, context: dict | None = None) -> SafetyVerdict:
        # Fixed allowlists: account ID, server name, secret and raw broker
        # request data can never escape via a model payload.
        now = datetime.now(UTC)
        details = _safety_payload(intent, now_utc=now)
        request_details = {
            key: str(request[key])[:80]
            for key in ("action", "symbol", "type", "volume", "price", "sl", "tp")
            if key in request and isinstance(request[key], (str, float, int, Decimal))
        }
        extras = {}
        for key in ("strategy_id", "account_trade_mode", "tick_age_ms", "bid", "ask",
                    "spread_points", "stop_distance_points", "target_distance_points"):
            if context and key in context and isinstance(context[key], (str, float, int)):
                extras[key] = str(context[key])[:100]
        digest_input = {"intent": details, "broker_request": request_details, "market": extras}
        digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        payload = {
            "model": self.model,
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": 0, "num_predict": 192},
            "format": _GateResponse.model_json_schema(),
            "messages": [
                {"role": "system", "content": (
                    "You are a PRE-EXECUTION SAFETY FILTER, not a trading agent. "
                    "Return ONLY the exact JSON schema. Evaluate an already risk-approved "
                    "order intent conservatively for conflicts visible in the supplied "
                    "fields (e.g. side vs protective stop and take profit, invalid prices, "
                    "nonpositive volume). ALLOW means no blocker detected in the supplied "
                    "facts; it never overrides broker/risk rules. BLOCK when a material "
                    "conflict is present or information seems incoherent. "
                    "Do not speculate about current news, account balances or trend. "
                    "Repeat intent_id and request_digest verbatim. "
                    "Reason codes must be short uppercase snake_case."
                )},
                {"role": "user", "content": json.dumps({
                    **digest_input, "request_digest": digest
                }, sort_keys=True, separators=(",", ":"))},
            ],
        }
        start = time.monotonic()
        try:
            with httpx.Client(
                trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(self.timeout_seconds, connect=min(2, self.timeout_seconds)),
            ) as client:
                response = client.post(self.endpoint + "/api/chat", json=payload)
                response.raise_for_status()
                if len(response.content) > 64000:
                    raise GateUnavailable("AI_GATE_RESPONSE_TOO_LARGE")
                envelope = response.json()
            if envelope.get("done") is not True:
                raise GateUnavailable("AI_GATE_INCOMPLETE_RESPONSE")
            content = envelope["message"]["content"]
            if not isinstance(content, str) or len(content) > 2048:
                raise GateUnavailable("AI_GATE_INVALID_RESPONSE")
            result = _GateResponse.model_validate_json(content)
            if result.intent_id != intent.intent_id or result.request_digest != digest:
                raise GateUnavailable("AI_GATE_REQUEST_MISMATCH")
            if not REASON.fullmatch(result.reason_code):
                raise GateUnavailable("AI_GATE_INVALID_REASON")
            latency = int((time.monotonic() - start) * 1000)
            return SafetyVerdict(
                allowed=result.decision == "ALLOW",
                reason_code=result.reason_code,
                model_fingerprint=self.fingerprint,
                latency_ms=latency,
                request_digest=digest,
            )
        except GateUnavailable:
            raise
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError, ValidationError, TypeError) as exc:
            raise GateUnavailable("AI_GATE_PROVIDER_UNAVAILABLE") from exc
