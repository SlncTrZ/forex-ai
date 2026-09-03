from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = "reviewer-v2"

SYSTEM_PROMPT = """You are a cautious market-review component inside a Forex research system.
You receive a frozen market/account snapshot that contains only information available at decision time.
Evaluate the snapshot and return only the required structured decision as one JSON object with no prose before or after it.
Do not invent missing market data. Do not choose lot size or bypass risk controls.
Use previous lessons only as evidence, never as immutable rules.
If evidence is insufficient or conflicting, choose NO_TRADE.
"""


def build_user_prompt(context: dict[str, Any]) -> str:
    example = {
        "action": "NO_TRADE",
        "confidence": 0.42,
        "thesis": "Current evidence is mixed.",
        "invalidation": "A fresh verified catalyst or cleaner technical setup would change the assessment.",
        "risk_flags": ["CURRENT_MACRO_UNVERIFIED"],
        "lesson_references": [],
        "web_search_used": True,
        "current_context_checks": [
            {
                "topic": "current macro/news",
                "status": "VERIFIED",
                "finding": "Concise current finding.",
                "sources": ["https://example.com/source"],
            }
        ],
    }
    return (
        "Review this frozen snapshot for research. Output exactly one JSON object and nothing else. "
        "The JSON must match this shape (values are only an example):\n"
        + json.dumps(example, ensure_ascii=False, separators=(",", ":"))
        + "\n\nFrozen context:\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
    )
