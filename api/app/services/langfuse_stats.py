"""
Langfuse stats query service.

Reads trace/generation/score data from Langfuse for display in the test panel.
Uses a TTL cache to avoid hammering the Langfuse API on every panel refresh.
"""

import logging
import time

from ..domain.constants import (
    LANGFUSE_STATS_CACHE_TTL_S,
    LANGFUSE_STATS_DISPLAY_LIMIT,
    LANGFUSE_STATS_TRACE_LIMIT,
    LLM_PRICING,
    LLM_PRICING_PER_MTOK,
)

logger = logging.getLogger(__name__)


class LangfuseStatsService:
    """Query Langfuse for observability stats shown in the test panel."""

    def __init__(self, tracer, model_name: str):
        self._tracer = tracer
        self._model_name = model_name
        self._cache: dict | None = None
        self._cache_time: float = 0

    @property
    def enabled(self) -> bool:
        from ..observability.tracer import LangfuseTracer
        return isinstance(self._tracer, LangfuseTracer) and self._tracer._enabled

    def get_stats(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "summary": None, "recent_traces": []}

        now = time.time()
        if self._cache and (now - self._cache_time) < LANGFUSE_STATS_CACHE_TTL_S:
            return self._cache

        try:
            result = self._fetch_stats()
            self._cache = result
            self._cache_time = now
            return result
        except Exception as e:
            logger.warning("Langfuse stats query failed: %s", e)
            return {"enabled": True, "summary": None, "recent_traces": []}

    def _fetch_stats(self) -> dict:
        langfuse = self._tracer.langfuse

        traces_resp = langfuse.fetch_traces(limit=LANGFUSE_STATS_TRACE_LIMIT)
        traces = traces_resp.data if hasattr(traces_resp, "data") else []

        total_tokens = 0
        total_input_tokens = 0
        total_output_tokens = 0
        judge_scores: list[float] = []
        latencies: list[float] = []
        recent: list[dict] = []

        for trace in traces:
            trace_id = trace.id if hasattr(trace, "id") else str(trace.get("id", ""))
            trace_name = trace.name if hasattr(trace, "name") else str(trace.get("name", ""))
            trace_ts = str(trace.timestamp if hasattr(trace, "timestamp") else trace.get("timestamp", ""))

            # Fetch observations (generations) for this trace
            trace_tokens_in = 0
            trace_tokens_out = 0
            trace_latency = 0.0

            try:
                obs_resp = langfuse.fetch_observations(trace_id=trace_id, type="GENERATION")
                observations = obs_resp.data if hasattr(obs_resp, "data") else []
                for obs in observations:
                    usage = obs.usage if hasattr(obs, "usage") else (obs.get("usage") or {})
                    if hasattr(usage, "input"):
                        trace_tokens_in += usage.input or 0
                        trace_tokens_out += usage.output or 0
                    elif isinstance(usage, dict):
                        trace_tokens_in += usage.get("input", 0) or 0
                        trace_tokens_out += usage.get("output", 0) or 0

                    obs_latency = obs.latency if hasattr(obs, "latency") else obs.get("latency", 0)
                    trace_latency += obs_latency or 0
            except Exception as e:
                logger.debug("Failed to fetch observations for trace %s: %s", trace_id, e)

            trace_total = trace_tokens_in + trace_tokens_out
            total_tokens += trace_total
            total_input_tokens += trace_tokens_in
            total_output_tokens += trace_tokens_out

            if trace_latency > 0:
                latencies.append(trace_latency)

            # Fetch judge score for this trace
            trace_score: float | None = None
            try:
                scores_resp = langfuse.fetch_scores(trace_id=trace_id, name="judge_score")
                scores = scores_resp.data if hasattr(scores_resp, "data") else []
                if scores:
                    s = scores[0]
                    trace_score = s.value if hasattr(s, "value") else s.get("value")
                    if trace_score is not None:
                        judge_scores.append(trace_score)
            except Exception:
                pass

            recent.append({
                "trace_id": trace_id,
                "name": trace_name,
                "timestamp": trace_ts,
                "tokens": trace_total,
                "latency_s": round(trace_latency, 2),
                "score": trace_score,
            })

        # Calculate cost using existing LLM_PRICING
        cost = self._estimate_cost(total_input_tokens, total_output_tokens)

        summary = {
            "total_traces": len(traces),
            "total_tokens": total_tokens,
            "cost_usd": round(cost, 4),
            "avg_judge_score": round(sum(judge_scores) / len(judge_scores), 2) if judge_scores else None,
            "avg_latency_s": round(sum(latencies) / len(latencies), 2) if latencies else None,
        }

        return {
            "enabled": True,
            "summary": summary,
            "recent_traces": recent[:LANGFUSE_STATS_DISPLAY_LIMIT],
        }

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        model = self._model_name.lower()
        for key, (input_price, output_price) in LLM_PRICING.items():
            if key in model:
                return (
                    (input_tokens / LLM_PRICING_PER_MTOK) * input_price
                    + (output_tokens / LLM_PRICING_PER_MTOK) * output_price
                )
        # Fallback: haiku pricing
        fallback_in, fallback_out = LLM_PRICING["haiku"]
        return (
            (input_tokens / LLM_PRICING_PER_MTOK) * fallback_in
            + (output_tokens / LLM_PRICING_PER_MTOK) * fallback_out
        )
