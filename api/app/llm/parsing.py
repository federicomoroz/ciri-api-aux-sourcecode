"""
JSON parsing utilities for LLM responses.

Single source of truth for stripping markdown code blocks and safely
parsing potentially-malformed JSON returned by the LLM.
"""

import json
import logging

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


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
                logger.warning("Failed to parse JSON from LLM response (len=%d), using fallback", len(text))
                return fallback
        logger.warning("No JSON structure found in LLM response (len=%d), using fallback", len(text))
        return fallback


def validate_llm_output(
    raw_text: str,
    model: type[BaseModel],
    fallback: dict | list,
) -> dict | list:
    """Parse LLM JSON response and validate against a Pydantic model.

    Returns model_dump() on success. On validation failure, logs WARNING and
    returns the raw parsed dict so guardrails can still operate.
    """
    parsed = parse_json_safely(raw_text, fallback)
    if parsed is fallback and not raw_text.strip():
        return fallback

    try:
        if isinstance(parsed, list):
            validated = [model.model_validate(item) for item in parsed]
            return [v.model_dump() for v in validated]
        validated = model.model_validate(parsed)
        return validated.model_dump()
    except ValidationError as e:
        logger.warning(
            "LLM output failed validation against %s: %s",
            model.__name__, e.errors(),
        )
        return parsed
