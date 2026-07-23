"""
Observability tracer abstraction.

Axis 7 (Observabilidad): every LLM call, API request, judge score,
and cache hit is traced for cost/quality/latency monitoring.

LangfuseTracer: sends data to Langfuse cloud dashboard.
NoOpTracer: used in tests and when langfuse_enabled=False.
"""

import datetime
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Tracer(Protocol):
    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        """Create a trace. Returns trace_id."""
        ...

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Log an LLM generation with token counts and latency."""
        ...

    def score(self, trace_id: str, name: str, value: float) -> None:
        """Attach a score to a trace (e.g., judge_score)."""
        ...


class LangfuseTracer:
    """Real Langfuse integration for production observability."""

    def __init__(self, public_key: str, secret_key: str, host: str):
        try:
            from langfuse import Langfuse
            self.langfuse = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
            self._enabled = True
        except ImportError:
            logger.warning("langfuse package not installed; observability disabled")
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        if not self._enabled:
            return ""
        try:
            t = self.langfuse.trace(name=name, input=input, output=output, metadata=metadata or {})
            return t.id
        except Exception as e:
            logger.warning("Langfuse trace failed: %s", e)
            return ""

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        try:
            end_time = datetime.datetime.now(datetime.timezone.utc)
            start_time = end_time - datetime.timedelta(milliseconds=latency_ms)
            self.langfuse.generation(
                name=name,
                model=model,
                input=input,
                output=output,
                usage={"input": tokens_in, "output": tokens_out},
                start_time=start_time,
                end_time=end_time,
                trace_id=trace_id,
            )
        except Exception as e:
            logger.warning("Langfuse generation failed: %s", e)

    def score(self, trace_id: str, name: str, value: float) -> None:
        if not self._enabled or not trace_id:
            return
        try:
            self.langfuse.score(trace_id=trace_id, name=name, value=value)
        except Exception as e:
            logger.warning("Langfuse score failed: %s", e)


class NoOpTracer:
    """No-op tracer for tests and when observability is disabled."""

    @property
    def enabled(self) -> bool:
        return False

    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        return ""

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        pass

    def score(self, trace_id: str, name: str, value: float) -> None:
        pass
