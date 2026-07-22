"""
JSON parsing utilities for LLM responses.

Single source of truth for stripping markdown code blocks and safely
parsing potentially-malformed JSON returned by the LLM.
"""

import json


def parse_json_safely(text: str, fallback: dict | list) -> dict | list:
    """Parse LLM JSON response, stripping markdown code blocks if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON within the text
        start = text.find("[") if isinstance(fallback, list) else text.find("{")
        end = text.rfind("]") + 1 if isinstance(fallback, list) else text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        return fallback
