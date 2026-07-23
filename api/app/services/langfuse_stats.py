"""
Langfuse stats query service.

Reads trace/generation/score data from Langfuse for display in the test panel.
Uses a TTL cache to avoid hammering the Langfuse API on every panel refresh.

IMPORTANT: Langfuse free tier has a 15 req/min rate limit per endpoint.
This service uses at most 3 API calls (traces + observations + scores)
instead of N+1 per-trace calls.
"""

import logging
import time
from collections import defaultdict

from ..domain.constants import (
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_STATS_CACHE_TTL_S,
    LANGFUSE_STATS_DISPLAY_LIMIT,
    LANGFUSE_STATS_FETCH_LIMIT,
    LANGFUSE_STATS_TRACE_LIMIT,
    LLM_PRICING,
    LLM_PRICING_FALLBACK_KEY,
    LLM_PRICING_PER_MTOK,
)
from ..observability.tracer import Tracer

logger = logging.getLogger(__name__)


class LangfuseStatsService:
    """Query Langfuse for observability stats shown in the test panel."""

    def __init__(self, tracer: Tracer, model_name: str):
        self._tracer = tracer
        self._model_name = model_name
        self._cache: dict | None = None
        self._cache_time: float = 0

    @property
    def enabled(self) -> bool:
        from ..observability.tracer import LangfuseTracer
        return isinstance(self._tracer, LangfuseTracer) and self._tracer.enabled

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

        # --- 1 API call: fetch traces ---
        traces_resp = langfuse.fetch_traces(limit=LANGFUSE_STATS_TRACE_LIMIT)
        traces = traces_resp.data if hasattr(traces_resp, "data") else []

        if not traces:
            return {
                "enabled": True,
                "summary": {"total_traces": 0, "total_tokens": 0, "cost_usd": 0, "avg_judge_score": None, "avg_latency_s": None},
                "recent_traces": [],
            }

        # --- 1 API call: fetch all recent observations (bulk, no per-trace) ---
        obs_by_trace: dict[str, list] = defaultdict(list)
        try:
            obs_resp = langfuse.fetch_observations(type=LANGFUSE_OBSERVATION_TYPE, limit=LANGFUSE_STATS_FETCH_LIMIT)
            observations = obs_resp.data if hasattr(obs_resp, "data") else []
            for obs in observations:
                tid = obs.trace_id if hasattr(obs, "trace_id") else obs.get("trace_id", "")
                if tid:
                    obs_by_trace[tid].append(obs)
        except Exception as e:
            logger.debug("Failed to fetch observations: %s", e)

        # --- 1 API call: fetch all recent scores (bulk) ---
        scores_by_trace: dict[str, float] = {}
        try:
            scores_resp = langfuse.fetch_scores(limit=LANGFUSE_STATS_FETCH_LIMIT)
            scores_list = scores_resp.data if hasattr(scores_resp, "data") else []
            for s in scores_list:
                tid = s.trace_id if hasattr(s, "trace_id") else s.get("trace_id", "")
                val = s.value if hasattr(s, "value") else s.get("value")
                if tid and val is not None:
                    scores_by_trace[tid] = val
        except Exception as e:
            logger.debug("Failed to fetch scores: %s", e)

        # --- Aggregate per trace ---
        total_input_tokens = 0
        total_output_tokens = 0
        judge_scores: list[float] = []
        latencies: list[float] = []
        recent: list[dict] = []

        for trace in traces:
            trace_id = trace.id if hasattr(trace, "id") else str(trace.get("id", ""))
            trace_name = trace.name if hasattr(trace, "name") else str(trace.get("name", ""))
            trace_ts = str(trace.timestamp if hasattr(trace, "timestamp") else trace.get("timestamp", ""))

            trace_tokens_in = 0
            trace_tokens_out = 0
            trace_latency = 0.0

            for obs in obs_by_trace.get(trace_id, []):
                usage = obs.usage if hasattr(obs, "usage") else (obs.get("usage") or {})
                if hasattr(usage, "input"):
                    trace_tokens_in += usage.input or 0
                    trace_tokens_out += usage.output or 0
                elif isinstance(usage, dict):
                    trace_tokens_in += usage.get("input", 0) or 0
                    trace_tokens_out += usage.get("output", 0) or 0

                obs_latency = obs.latency if hasattr(obs, "latency") else obs.get("latency", 0)
                trace_latency += obs_latency or 0

            trace_total = trace_tokens_in + trace_tokens_out
            total_input_tokens += trace_tokens_in
            total_output_tokens += trace_tokens_out

            if trace_latency > 0:
                latencies.append(trace_latency)

            trace_score = scores_by_trace.get(trace_id)
            if trace_score is not None:
                judge_scores.append(trace_score)

            recent.append({
                "trace_id": trace_id,
                "name": trace_name,
                "timestamp": trace_ts,
                "tokens": trace_total,
                "latency_s": round(trace_latency, 2),
                "score": trace_score,
            })

        total_tokens = total_input_tokens + total_output_tokens
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
        fallback_in, fallback_out = LLM_PRICING[LLM_PRICING_FALLBACK_KEY]
        return (
            (input_tokens / LLM_PRICING_PER_MTOK) * fallback_in
            + (output_tokens / LLM_PRICING_PER_MTOK) * fallback_out
        )
