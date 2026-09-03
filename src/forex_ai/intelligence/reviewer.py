from __future__ import annotations

from typing import Any, Protocol

from forex_ai.intelligence.schemas import LLMUsage, ReviewDecision


class ReviewerProvider(Protocol):
    name: str
    model: str

    def review(self, context: dict[str, Any]) -> tuple[ReviewDecision, LLMUsage]: ...


class MockReviewer:
    """Zero-cost provider for end-to-end plumbing tests."""

    name = "mock"
    model = "no-trade"

    def review(self, context: dict[str, Any]) -> tuple[ReviewDecision, LLMUsage]:
        symbol = context.get("symbol", "unknown")
        return (
            ReviewDecision(
                action="NO_TRADE",
                confidence=0.0,
                thesis=f"Mock reviewer: no real model called for {symbol}.",
                invalidation="Not applicable in mock mode.",
                risk_flags=["MOCK_PROVIDER"],
                lesson_references=[],
            ),
            LLMUsage(),
        )
