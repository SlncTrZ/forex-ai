from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CurrentContextCheck(BaseModel):
    topic: str
    status: Literal["VERIFIED", "UNVERIFIED", "NOT_NEEDED"]
    finding: str = ""
    sources: list[str] = Field(default_factory=list)


class ReviewDecision(BaseModel):
    action: Literal["BUY", "SELL", "NO_TRADE"]
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    invalidation: str
    risk_flags: list[str] = Field(default_factory=list)
    lesson_references: list[int] = Field(default_factory=list)
    web_search_used: bool = False
    current_context_checks: list[CurrentContextCheck] = Field(default_factory=list)


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_miss_tokens: int = 0
    api_cost_usd: float = 0.0
    latency_ms: int = 0
    request_count: int = 0
