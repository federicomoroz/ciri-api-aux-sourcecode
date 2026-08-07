"""
Unit tests for LangfuseStatsService.

Covers: disabled tracer, enabled with mock data, TTL cache, error handling.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from api.app.observability.tracer import NoOpTracer
from api.app.services.langfuse_stats import LangfuseStatsService


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

        # Observations (generations) — bulk fetch, must include trace_id
        mock_obs = MagicMock()
        mock_obs.trace_id = "trace-001"
        mock_obs.usage = MagicMock()
        mock_obs.usage.input = 500
        mock_obs.usage.output = 200
        mock_obs.latency = 2.5

        obs_resp = MagicMock()
        obs_resp.data = [mock_obs]
        mock_tracer.langfuse.fetch_observations.return_value = obs_resp

        # Scores — bulk fetch via client.score.get(), must include trace_id
        mock_score = MagicMock()
        mock_score.trace_id = "trace-001"
        mock_score.value = 8.5

        scores_resp = MagicMock()
        scores_resp.data = [mock_score]
        mock_tracer.langfuse.client.score.get.return_value = scores_resp

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

        service.get_stats()
        # Force cache expiry
        service._cache_time = time.time() - 60
        service.get_stats()

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
        mock_tracer.langfuse.client.score.get.return_value = scores_resp

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


class TestCostoPorModelo:
    """El costo se cotiza con el modelo de cada llamada, no con uno solo.

    Regresion: se sumaban todos los tokens de la traza y se les aplicaba
    `settings.llm_model` — Haiku. En la configuracion de produccion dos de las
    tres llamadas corren en Sonnet, que sale casi cuatro veces mas: el panel
    informaba un costo sistematicamente bajo.
    """

    @pytest.fixture
    def mock_tracer(self):
        tracer = MagicMock()
        tracer._enabled = True
        tracer.__class__.__name__ = "LangfuseTracer"
        return tracer

    @staticmethod
    def _observacion(modelo: str, entrada: int, salida: int):
        obs = MagicMock()
        obs.trace_id = "trace-001"
        obs.usage = MagicMock()
        obs.usage.input = entrada
        obs.usage.output = salida
        obs.latency = 1.0
        obs.model = modelo
        return obs

    def _stats(self, mock_tracer, observaciones):
        traza = MagicMock()
        traza.id = "trace-001"
        traza.name = "resolve_chargeback"
        traza.timestamp = "2024-01-01T12:00:00Z"

        trazas = MagicMock()
        trazas.data = [traza]
        mock_tracer.langfuse.fetch_traces.return_value = trazas

        obs_resp = MagicMock()
        obs_resp.data = observaciones
        mock_tracer.langfuse.fetch_observations.return_value = obs_resp

        puntajes = MagicMock()
        puntajes.data = []
        mock_tracer.langfuse.client.score.get.return_value = puntajes

        with patch(
            "api.app.services.langfuse_stats.LangfuseStatsService.enabled",
            new_callable=lambda: property(lambda self: True),
        ):
            svc = LangfuseStatsService(mock_tracer, "claude-haiku-4-5-20251001")
            return svc.get_stats()

    def test_cada_llamada_paga_su_propia_tarifa(self, mock_tracer):
        result = self._stats(mock_tracer, [
            self._observacion("claude-haiku-4-5-20251001", 1_000_000, 0),   # USD 0.80
            self._observacion("claude-sonnet-4-6", 1_000_000, 0),           # USD 3.00
        ])
        assert result["summary"]["cost_usd"] == pytest.approx(3.80, abs=0.001)

    def test_cotizar_todo_a_un_solo_modelo_subestimaba(self, mock_tracer):
        """Con la formula vieja, 2M tokens de entrada daban USD 1.60."""
        result = self._stats(mock_tracer, [
            self._observacion("claude-sonnet-4-6", 1_000_000, 0),
            self._observacion("claude-sonnet-4-6", 1_000_000, 0),
        ])
        assert result["summary"]["cost_usd"] == pytest.approx(6.00, abs=0.001)

    def test_sin_modelo_en_la_observacion_usa_el_configurado(self, mock_tracer):
        obs = self._observacion("", 1_000_000, 0)
        obs.model = ""
        result = self._stats(mock_tracer, [obs])
        assert result["summary"]["cost_usd"] == pytest.approx(0.80, abs=0.001)
