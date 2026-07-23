"""
LLM client abstraction.

Protocol for dependency injection + Anthropic implementation.
All external API calls are wrapped with error handling and logging.
"""

import logging
import time
from typing import Protocol, runtime_checkable

import anthropic

from ..domain.constants import (
    LLM_DEFAULT_MAX_RETRIES,
    LLM_DEFAULT_MAX_TOKENS,
    LLM_DEFAULT_TEMPERATURE,
    LLM_TRUNCATION_LENGTH,
    SECONDS_TO_MS,
    TRACE_LLM_CALL,
)
from ..observability.tracer import Tracer

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = LLM_DEFAULT_MAX_TOKENS,
        temperature: float = LLM_DEFAULT_TEMPERATURE,
        trace_id: str | None = None,
    ) -> str:
        """Send system + user prompt, return text response."""
        ...


class AnthropicClient:
    def __init__(self, api_key: str, model: str, tracer: Tracer | None = None, max_retries: int = LLM_DEFAULT_MAX_RETRIES):
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=max_retries)
        self.model = model
        self.tracer = tracer

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = LLM_DEFAULT_MAX_TOKENS,
        temperature: float = LLM_DEFAULT_TEMPERATURE,
        trace_id: str | None = None,
    ) -> str:
        start = time.time()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            logger.error("Anthropic API error: %s", e)
            raise
        except Exception as e:
            logger.error("Unexpected LLM error: %s", e)
            raise

        latency_ms = (time.time() - start) * SECONDS_TO_MS
        text = response.content[0].text

        if self.tracer:
            self.tracer.generation(
                name=TRACE_LLM_CALL,
                model=self.model,
                input=user[:LLM_TRUNCATION_LENGTH],
                output=text[:LLM_TRUNCATION_LENGTH],
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

        logger.info(
            "LLM call completed: model=%s tokens_in=%d tokens_out=%d latency=%.0fms",
            self.model, response.usage.input_tokens, response.usage.output_tokens, latency_ms,
        )
        return text
