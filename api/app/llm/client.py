import time
from typing import Protocol, runtime_checkable

import anthropic

from ..domain.constants import LLM_TRUNCATION_LENGTH
from ..observability.tracer import Tracer


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        trace_id: str | None = None,
    ) -> str:
        """Send system + user prompt, return text response."""
        ...


class AnthropicClient:
    def __init__(self, api_key: str, model: str, tracer: Tracer | None = None):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.tracer = tracer

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.3,
        trace_id: str | None = None,
    ) -> str:
        start = time.time()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency_ms = (time.time() - start) * 1000
        text = response.content[0].text

        if self.tracer:
            self.tracer.generation(
                name="llm_call",
                model=self.model,
                input=user[:LLM_TRUNCATION_LENGTH],
                output=text[:LLM_TRUNCATION_LENGTH],
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                latency_ms=latency_ms,
                trace_id=trace_id,
            )

        return text
