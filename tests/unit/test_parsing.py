"""Unit tests for llm/parsing.py — parse_json_safely + validate_llm_output."""

import json

import pytest
from api.app.llm.parsing import parse_json_safely, validate_llm_output
from api.app.domain.models import (
    JudgeEvaluationOutput,
    PolicyVerdictOutput,
    ResolutionOutput,
)


class TestParseJsonSafely:
    """Tests for robust JSON parsing from LLM responses."""

    def test_clean_json_dict(self):
        result = parse_json_safely('{"key": "value"}', {})
        assert result == {"key": "value"}

    def test_clean_json_array(self):
        result = parse_json_safely('[1, 2, 3]', [])
        assert result == [1, 2, 3]

    def test_markdown_wrapped_json(self):
        text = '```json\n{"key": "value"}\n```'
        result = parse_json_safely(text, {})
        assert result == {"key": "value"}

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"key": "value"}\nEnd of response.'
        result = parse_json_safely(text, {})
        assert result == {"key": "value"}

    def test_array_with_surrounding_text(self):
        text = 'The analysis:\n[{"code": "POL-001", "verdict": "PASS"}]\nDone.'
        result = parse_json_safely(text, [])
        assert result == [{"code": "POL-001", "verdict": "PASS"}]

    def test_invalid_json_returns_fallback_dict(self):
        result = parse_json_safely("this is not json at all", {"default": True})
        assert result == {"default": True}

    def test_invalid_json_returns_fallback_list(self):
        result = parse_json_safely("not json", [])
        assert result == []

    def test_empty_string_returns_fallback(self):
        result = parse_json_safely("", {"empty": True})
        assert result == {"empty": True}

    def test_nested_json(self):
        text = '{"resolution": {"action": "REJECT", "confidence": 0.95}}'
        result = parse_json_safely(text, {})
        assert result["resolution"]["action"] == "REJECT"

    def test_whitespace_padded(self):
        text = '   \n  {"key": "value"}  \n  '
        result = parse_json_safely(text, {})
        assert result == {"key": "value"}


class TestValidateLLMOutput:
    """Tests for validate_llm_output — structural validation of LLM JSON."""

    def test_valid_resolution(self):
        raw = json.dumps({
            "transaction_id": "TXN-001",
            "recommended_action": "REJECT",
            "confidence": 0.95,
            "justification": "Crypto transaction blocked by policy.",
            "risk_level": "BLOCKER",
            "compensation_applicable": False,
            "compensation_amount_usd": 0.0,
            "next_steps": ["Notify client"],
            "requires_hitl": False,
        })
        result = validate_llm_output(raw, ResolutionOutput, {})
        assert result["recommended_action"] == "REJECT"
        assert result["confidence"] == 0.95
        assert result["risk_level"] == "BLOCKER"

    def test_invalid_enum_returns_raw_parsed(self):
        """Invalid enum value → logs warning, returns raw parsed dict (non-breaking)."""
        raw = json.dumps({
            "recommended_action": "BANANA",
            "confidence": 0.5,
            "risk_level": "MEDIUM",
        })
        result = validate_llm_output(raw, ResolutionOutput, {})
        # Should return the raw parsed dict, not the fallback
        assert result["recommended_action"] == "BANANA"

    def test_parse_failure_returns_fallback(self):
        """Completely invalid JSON → returns fallback."""
        result = validate_llm_output("this is not json", ResolutionOutput, {})
        assert result == {}

    def test_list_of_verdicts(self):
        """Valid list of PolicyVerdictOutput → typed list."""
        raw = json.dumps([
            {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Crypto blocked"},
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Low fraud score"},
        ])
        result = validate_llm_output(raw, PolicyVerdictOutput, [])
        assert len(result) == 2
        assert result[0]["verdict"] == "BLOCKER"
        assert result[1]["verdict"] == "FAIL"

    def test_extra_fields_ignored(self):
        """LLM may return unexpected fields — extra='ignore' drops them silently."""
        raw = json.dumps({
            "overall_score": 8.5,
            "criteria": {"policy_consistency": 9.0},
            "approved": True,
            "strengths": ["Good"],
            "weaknesses": [],
            "unexpected_field": "should be ignored",
            "another_extra": 42,
        })
        result = validate_llm_output(raw, JudgeEvaluationOutput, {})
        assert result["overall_score"] == 8.5
        assert result["approved"] is True
        assert "unexpected_field" not in result
