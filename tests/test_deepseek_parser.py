import pytest

from forex_ai.intelligence.deepseek import DeepSeekError
from forex_ai.intelligence.deepseek_web import parse_review_decision


VALID_JSON = r'''{
  "action": "NO_TRADE",
  "confidence": 0.72,
  "thesis": "Current event risk is elevated.",
  "invalidation": "A cleaner setup after the event would change the view.",
  "risk_flags": ["EVENT_RISK"],
  "lesson_references": [],
  "web_search_used": true,
  "current_context_checks": [
    {
      "topic": "NFP",
      "status": "VERIFIED",
      "finding": "Release is imminent.",
      "sources": ["https://example.com"]
    }
  ]
}'''


def test_parser_accepts_pure_json():
    decision = parse_review_decision(VALID_JSON)
    assert decision.action == "NO_TRADE"
    assert decision.web_search_used is True


def test_parser_extracts_only_schema_valid_json_from_prose_and_fence():
    text = "analysis before\n```json\n" + VALID_JSON + "\n```\nextra text"
    decision = parse_review_decision(text)
    assert decision.action == "NO_TRADE"
    assert decision.confidence == 0.72


def test_parser_rejects_unrelated_json_objects():
    with pytest.raises(DeepSeekError):
        parse_review_decision('prose {"foo":"bar"} more prose')
