from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CurrentContextCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    status: Literal["VERIFIED", "UNVERIFIED", "NOT_NEEDED"]
    finding: str = ""
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_requires_source(self):
        if self.status == "VERIFIED" and not self.sources:
            raise ValueError("VERIFIED current-context check requires at least one source")
        return self


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["BUY", "SELL", "NO_TRADE"]
    confidence: float = Field(ge=0.0, le=1.0)
    thesis: str
    invalidation: str
    risk_flags: list[str] = Field(default_factory=list)
    lesson_references: list[int] = Field(default_factory=list)
    web_search_used: bool = False
    current_context_checks: list[CurrentContextCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def verified_context_requires_web_search(self):
        if any(check.status == "VERIFIED" for check in self.current_context_checks) and not self.web_search_used:
            raise ValueError("verified current-context checks require web_search_used=true")
        return self


class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_miss_tokens: int = 0
    api_cost_usd: float = 0.0
    latency_ms: int = 0
    request_count: int = 0
