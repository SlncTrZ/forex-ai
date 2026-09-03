from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from forex_ai.intelligence.deepseek import DeepSeekError, estimate_deepseek_cost, load_api_key
from forex_ai.intelligence.prompts import SYSTEM_PROMPT, build_user_prompt
from forex_ai.intelligence.schemas import LLMUsage, ReviewDecision


def parse_review_decision(text: str) -> ReviewDecision:
    """Accept only JSON objects that validate against ReviewDecision.

    DeepSeek may occasionally prepend prose even when JSON schema is requested after tool use.
    We ignore prose but never accept an object unless the full Pydantic schema validates.
    """
    stripped = text.strip()
    try:
        return ReviewDecision.model_validate_json(stripped)
    except Exception:
        pass

    candidates: list[str] = []
    depth = 0
    start: int | None = None
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : idx + 1])
                start = None

    last_error: Exception | None = None
    for candidate in reversed(candidates):
        try:
            data = json.loads(candidate)
            return ReviewDecision.model_validate(data)
        except Exception as exc:
            last_error = exc
    raise DeepSeekError(f"No valid ReviewDecision JSON object found; last_error={last_error}")


class DeepSeekWebReviewer:
    """DeepSeek V4 Flash reviewer using Responses API + server-side web search.

    MT5/account context is frozen by Forex-AI immediately before this call.
    DeepSeek may use its hosted web_search tool to verify current macro/news context.
    This class has no trading/execution tool.
    """

    name = "deepseek"
    model = "deepseek-v4-flash"

    def __init__(
        self,
        *,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 90.0,
        reasoning_effort: str = "none",
        max_output_tokens: int = 4000,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.api_key = load_api_key()
        self.last_web_trace: list[dict[str, Any]] = []
        self.last_raw_response: str = ""
        self.last_response_id: str | None = None
        self.last_usage: LLMUsage | None = None

    def review(self, context: dict[str, Any]) -> tuple[ReviewDecision, LLMUsage, str]:
        self.last_web_trace = []
        self.last_raw_response = ""
        self.last_response_id = None
        self.last_usage = None
        started_ns = time.monotonic_ns()
        request_time = datetime.now(timezone.utc)

        instructions = (
            SYSTEM_PROMPT
            + "\nThe MT5/account snapshot and decision_clock in the user input are authoritative current state. "
            "Never replace live broker data with remembered prices or training-time facts. "
            "Use the built-in web_search tool when current macroeconomic, central-bank, yield, geopolitical, "
            "or breaking-news context could materially change the assessment. Prefer primary/official sources "
            "and recent reputable reporting. If current context remains unverified, include CURRENT_MACRO_UNVERIFIED "
            "and prefer NO_TRADE. Do not choose position size and do not attempt to execute a trade. "
            "You MUST perform at least one targeted web search before finalizing. Keep browsing concise: prefer one search pass with a few focused queries and open only the most relevant pages. "
            "For macro claims, prefer primary sources (Federal Reserve, BLS, US Treasury, ECB, Bank of England, official statistical agencies) and then high-quality current reporting such as Reuters/Bloomberg/FT/WSJ/CNBC. "
            "Do not mark a current-context check VERIFIED when support comes only from low-quality blogs/aggregators; mark it UNVERIFIED instead. "
            "Set web_search_used=true and record concise checks/sources in current_context_checks."
        )

        schema = ReviewDecision.model_json_schema()
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": build_user_prompt(context),
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
            "stream": False,
            "tools": [{"type": "web_search"}],
            "tool_choice": "auto",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "forex_review_decision",
                    "schema": schema,
                }
            },
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.base_url}/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise DeepSeekError(f"DeepSeek Responses HTTP {response.status_code}: {response.text[:1500]}")
        data = response.json()
        self.last_response_id = data.get("id")

        usage = data.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        cached_tokens = int((usage.get("input_tokens_details") or {}).get("cached_tokens") or 0)
        miss_tokens = max(0, input_tokens - cached_tokens)
        output_tokens = int(usage.get("output_tokens") or 0)
        cost = estimate_deepseek_cost(
            model=self.model,
            request_time_utc=request_time,
            cache_hit_tokens=cached_tokens,
            cache_miss_tokens=miss_tokens,
            output_tokens=output_tokens,
        )
        latency_ms = int((time.monotonic_ns() - started_ns) / 1_000_000)
        self.last_usage = LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_miss_tokens=miss_tokens,
            api_cost_usd=cost,
            latency_ms=latency_ms,
            request_count=1,
        )

        if data.get("status") != "completed":
            raise DeepSeekError(
                f"DeepSeek response status={data.get('status')} error={data.get('error')} incomplete={data.get('incomplete_details')}"
            )

        raw_final = ""
        for item in data.get("output") or []:
            item_type = item.get("type")
            if item_type == "web_search_call":
                self.last_web_trace.append(
                    {
                        "id": item.get("id"),
                        "status": item.get("status"),
                        "action": item.get("action"),
                    }
                )
            elif item_type == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        raw_final += str(part.get("text") or "")

        self.last_raw_response = raw_final
        if not self.last_web_trace:
            raise DeepSeekError("DeepSeek returned a final response without required web_search evidence")
        if not raw_final:
            raise DeepSeekError("DeepSeek Responses returned no output_text")

        try:
            decision = parse_review_decision(raw_final)
        except Exception as exc:
            raise DeepSeekError(f"Invalid structured DeepSeek response: {exc}; raw={raw_final[:1500]}") from exc

        usage_obj = self.last_usage or LLMUsage()
        raw_hash = hashlib.sha256(raw_final.encode("utf-8")).hexdigest()
        return decision, usage_obj, raw_hash
