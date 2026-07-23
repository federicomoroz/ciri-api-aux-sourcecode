"""
Unit tests for LangfuseStatsService.

Covers: disabled tracer, enabled with mock data, TTL cache, error handling.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from api.app.services.langfuse_stats import LangfuseStatsService
from api.app.observability.tracer import NoOpTracer


class TestLangfuseStatsDisabled:
    """When tracer is NoOp (Langfuse disabled), service returns disabled response."""

    def test_returns_disabled_when_noop_tracer(self):
        service = LangfuseStatsService(NoOpTracer(), "claude-haiku-4-5-20251001")
        result = service.get_stats()
        assert result["enabled"] is False
        assert result["summary"] is None
        assert result["recent_traces"] == []

    def test_enabled_property_false_for_noop(self):
        service = LangfuseStatsService(NoOpTracer(), "claude-haiku-4-5-20251001")
        assert service.enabled is False


class TestLangfuseStatsEnabled:
    """When tracer is LangfuseTracer (enabled), service queries and returns stats."""

    @pytest.fixture
    def mock_tracer(self):
        """Create a mock that looks like a LangfuseTracer."""
        tracer = MagicMock()
        tracer._enabled = True
        # Make isinstance check work for LangfuseTracer
        tracer.__class__.__name__ = "LangfuseTracer"
        return tracer

    @pytest.fixture
    def service(self, mock_tracer):
        # Patch the isinstance check
        with patch("api.app.services.langfuse_stats.LangfuseStatsService.enabled", new_callable=lambda: property(lambda self: True)):
            svc = LangfuseStatsService(mock_tracer, "claude-haiku-4-5-20251001")
            yield svc

    def _setup_langfuse_mocks(self, mock_tracer):
        """Set up mock Langfuse SDK responses."""
        mock_trace = MagicMock()
        mock_trace.id = "trace-001"
        mock_trace.name = "resolve_chargeback"
        mock_trace.timestamp = "2024-01-01T12:00:00Z"

        traces_resp = MagicMock()
        traces_resp.data = [mock_trace]
        mock_tracer.langfuse.fetch_traces.return_value = traces_resp

        # Observations (generations)
        mock_obs = MagicMock()
        mock_obs.usage = MagicMock()
        mock_obs.usage.input = 500
        mock_obs.usage.output = 200
        mock_obs.latency = 2.5

        obs_resp = MagicMock()
        obs_resp.data = [mock_obs]
        mock_tracer.langfuse.fetch_observations.return_value = obs_resp

        # Scores
        mock_score = MagicMock()
        mock_score.value = 8.5

        scores_resp = MagicMock()
        scores_resp.data = [mock_score]
        mock_tracer.langfuse.fetch_scores.return_value = scores_resp

    def test_returns_enabled_with_summary(self, service, mock_tracer):
        self._setup_langfuse_mocks(mock_tracer)
        result = service.get_stats()

        assert result["enabled"] is True
        assert result["summary"] is not None
        assert result["summary"]["total_traces"] == 1
        assert result["summary"]["total_tokens"] == 700
        assert result["summary"]["avg_judge_score"] == 8.5
        assert result["summary"]["avg_latency_s"] == 2.5
        assert result["summary"]["cost_usd"] > 0
        assert len(result["recent_traces"]) == 1
        assert result["recent_traces"][0]["trace_id"] == "trace-001"
        assert result["recent_traces"][0]["tokens"] == 700

    def test_cache_returns_same_result_within_ttl(self, service, mock_tracer):
        self._setup_langfuse_mocks(mock_tracer)

        result1 = service.get_stats()
        # Modify mock to return different data
        mock_tracer.langfuse.fetch_traces.return_value.data = []
        result2 = service.get_stats()

        # Should get cached result (same as first)
        assert result1 == result2
        # fetch_traces called only once due to cache
        assert mock_tracer.langfuse.fetch_traces.call_count == 1

    def test_cache_expires_after_ttl(self, service, mock_tracer):
        self._setup_langfuse_mocks(mock_tracer)

        result1 = service.get_stats()
        # Force cache expiry
        service._cache_time = time.time() - 60
        result2 = service.get_stats()

        # fetch_traces called twice (cache expired)
        assert mock_tracer.langfuse.fetch_traces.call_count == 2

    def test_graceful_error_handling(self, service, mock_tracer):
        mock_tracer.langfuse.fetch_traces.side_effect = Exception("API error")
        result = service.get_stats()

        assert result["enabled"] is True
        assert result["summary"] is None
        assert result["recent_traces"] == []

    def test_handles_missing_score(self, service, mock_tracer):
        self._setup_langfuse_mocks(mock_tracer)
        # No scores for this trace
        scores_resp = MagicMock()
        scores_resp.data = []
        mock_tracer.langfuse.fetch_scores.return_value = scores_resp

        result = service.get_stats()
        assert result["summary"]["avg_judge_score"] is None
        assert result["recent_traces"][0]["score"] is None

    def test_cost_uses_model_pricing(self, mock_tracer):
        with patch("api.app.services.langfuse_stats.LangfuseStatsService.enabled", new_callable=lambda: property(lambda self: True)):
            svc = LangfuseStatsService(mock_tracer, "claude-sonnet-4-6")
            self._setup_langfuse_mocks(mock_tracer)
            result = svc.get_stats()
            # Sonnet pricing: (500/1M)*3.00 + (200/1M)*15.00
            expected = (500 / 1_000_000) * 3.00 + (200 / 1_000_000) * 15.00
            assert abs(result["summary"]["cost_usd"] - expected) < 0.0001
