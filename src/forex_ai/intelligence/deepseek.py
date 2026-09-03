from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from forex_ai.config import RuntimeConfig
from forex_ai.intelligence.context_builder import build_symbol_context
from forex_ai.intelligence.prompts import SYSTEM_PROMPT, build_user_prompt
from forex_ai.intelligence.schemas import LLMUsage, ReviewDecision
from forex_ai.learning.lesson_selector import select_lessons
from forex_ai.mt5.client import MT5Client

DEFAULT_KEY_FILE = Path.home() / ".config" / "forex-ai" / "deepseek_api_key"


class DeepSeekError(RuntimeError):
    pass


def load_api_key() -> str:
    value = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value
    if DEFAULT_KEY_FILE.exists():
        value = DEFAULT_KEY_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise DeepSeekError(
        f"DeepSeek API key not configured. Put it in {DEFAULT_KEY_FILE} (chmod 600) "
        "or set DEEPSEEK_API_KEY."
    )


def is_peak_utc(moment: datetime) -> bool:
    moment = moment.astimezone(timezone.utc)
    if moment.weekday() >= 5:
        return False
    hour = moment.hour + moment.minute / 60 + moment.second / 3600
    return (1 <= hour < 4) or (6 <= hour < 10)


def estimate_deepseek_cost(
    *,
    model: str,
    request_time_utc: datetime,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
) -> float:
    peak = is_peak_utc(request_time_utc)
    rates = {
        "deepseek-v4-flash": {
            False: (0.007, 0.22, 0.66),
            True: (0.014, 0.44, 1.32),
        },
        "deepseek-v4-pro": {
            False: (0.022, 0.66, 1.98),
            True: (0.044, 1.32, 3.96),
        },
    }
    if model not in rates:
        raise DeepSeekError(f"Unknown pricing for model {model}")
    hit_rate, miss_rate, output_rate = rates[model][peak]
    return (
        cache_hit_tokens * hit_rate
        + cache_miss_tokens * miss_rate
        + output_tokens * output_rate
    ) / 1_000_000


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Refresh the authoritative current UTC and Asia/Ho_Chi_Minh clocks before making a time-sensitive decision.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_market_context",
            "description": "Refresh live MT5 account, tick, contract, open positions, M5/M15/H1/H4 candles and technical features for the current symbol.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_state",
            "description": "Refresh live MT5 account state and all open positions.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_relevant_lessons",
            "description": "Retrieve the most relevant active lessons from the local SQLite journal for the current symbol.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


class DeepSeekReviewer:
    name = "deepseek"

    def __init__(
        self,
        *,
        cfg: RuntimeConfig,
        mt5: MT5Client,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 45.0,
        max_tokens: int = 800,
        max_tool_rounds: int = 2,
    ):
        self.cfg = cfg
        self.mt5 = mt5
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.max_tool_rounds = max_tool_rounds
        self.api_key = load_api_key()
        self.last_tool_trace: list[dict[str, Any]] = []
        self.last_raw_response: str = ""

    def _tool_result(self, name: str, context: dict[str, Any]) -> dict[str, Any]:
        base_symbol = str(context.get("base_symbol") or "")
        if name == "get_current_time":
            now = datetime.now(timezone.utc)
            local = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
            return {
                "utc_iso": now.isoformat(timespec="microseconds"),
                "utc_epoch_ms": int(now.timestamp() * 1000),
                "local_timezone": "Asia/Ho_Chi_Minh",
                "local_iso": local.isoformat(timespec="microseconds"),
            }
        if name == "refresh_market_context":
            return build_symbol_context(self.mt5, self.cfg, base_symbol)
        if name == "get_account_state":
            return {
                "account": self.mt5.account_info(),
                "positions": self.mt5.positions(),
                "refreshed_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            }
        if name == "get_relevant_lessons":
            return {
                "lessons": select_lessons(self.cfg.db_path, symbol=context.get("symbol"), limit=5),
                "refreshed_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            }
        raise DeepSeekError(f"Unsupported tool call: {name}")

    def review(self, context: dict[str, Any]) -> tuple[ReviewDecision, LLMUsage, str]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\nCurrent-time rule: the injected decision_clock and tool results are authoritative. "
                "Never assume today's macro/news/geopolitical conditions from training memory. "
                "External macro/news tooling is not yet available in this runtime; when material current macro context is required and cannot be verified, include CURRENT_MACRO_UNVERIFIED in risk_flags and prefer NO_TRADE. "
                "You may call the provided read-only tools to refresh time, MT5 market context, account state, or lessons before finalizing. "
                "Your final response MUST be a JSON object matching the requested decision fields.",
            },
            {"role": "user", "content": build_user_prompt(context)},
        ]

        self.last_tool_trace = []
        self.last_raw_response = ""
        total_input = total_output = total_hit = total_miss = 0
        total_cost = 0.0
        started_ns = time.monotonic_ns()
        raw_final = ""

        with httpx.Client(timeout=self.timeout_seconds) as client:
            for round_index in range(self.max_tool_rounds + 1):
                request_time = datetime.now(timezone.utc)
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "tools": TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                    "thinking": {"type": "disabled"},
                    "response_format": {"type": "json_object"},
                    "max_tokens": self.max_tokens,
                    "stream": False,
                }
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                if response.status_code >= 400:
                    raise DeepSeekError(f"DeepSeek HTTP {response.status_code}: {response.text[:1000]}")
                data = response.json()
                usage = data.get("usage", {}) or {}
                prompt_tokens = int(usage.get("prompt_tokens") or 0)
                completion_tokens = int(usage.get("completion_tokens") or 0)
                hit_tokens = int(usage.get("prompt_cache_hit_tokens") or 0)
                miss_tokens = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt_tokens - hit_tokens))
                total_input += prompt_tokens
                total_output += completion_tokens
                total_hit += hit_tokens
                total_miss += miss_tokens
                total_cost += estimate_deepseek_cost(
                    model=self.model,
                    request_time_utc=request_time,
                    cache_hit_tokens=hit_tokens,
                    cache_miss_tokens=miss_tokens,
                    output_tokens=completion_tokens,
                )

                choices = data.get("choices") or []
                if not choices:
                    raise DeepSeekError("DeepSeek returned no choices")
                message = choices[0].get("message") or {}
                tool_calls = message.get("tool_calls") or []

                if tool_calls:
                    if round_index >= self.max_tool_rounds:
                        raise DeepSeekError("DeepSeek exceeded configured tool-call rounds")
                    assistant_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": tool_calls,
                    }
                    messages.append(assistant_msg)
                    for call in tool_calls:
                        function = call.get("function") or {}
                        name = str(function.get("name") or "")
                        result = self._tool_result(name, context)
                        self.last_tool_trace.append(
                            {
                                "called_at_utc": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                                "tool_call_id": call.get("id"),
                                "name": name,
                                "arguments": function.get("arguments"),
                                "result": result,
                            }
                        )
                        if name == "refresh_market_context":
                            context = result
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.get("id"),
                                "content": json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str),
                            }
                        )
                    continue

                raw_final = str(message.get("content") or "")
                self.last_raw_response = raw_final
                try:
                    parsed = json.loads(raw_final)
                    decision = ReviewDecision.model_validate(parsed)
                except Exception as exc:
                    raise DeepSeekError(f"Invalid DeepSeek decision JSON: {exc}; raw={raw_final[:1000]}") from exc

                latency_ms = int((time.monotonic_ns() - started_ns) / 1_000_000)
                usage_obj = LLMUsage(
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cached_tokens=total_hit,
                    cache_miss_tokens=total_miss,
                    api_cost_usd=total_cost,
                    latency_ms=latency_ms,
                    request_count=round_index + 1,
                )
                raw_hash = hashlib.sha256(raw_final.encode("utf-8")).hexdigest()
                return decision, usage_obj, raw_hash

        raise DeepSeekError("DeepSeek did not return a final decision")
