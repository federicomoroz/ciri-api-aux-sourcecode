"""
Integration tests for all HTTP routes not covered by test_full_flow.py.

Covers:
- GET /api/transactions (list)
- GET /api/clients/{id}/history (404 case)
- GET /api/merchants/{name}/risk (unknown merchant)
- POST /api/sla/check (edge cases: VIP, non-LATAM, breached)
- POST /api/analyze/judge (low score → not approved)
- POST /api/feedback (with resolution → auto-index trigger)
- GET /api/cache/lookup (cache enabled + hit/miss)
- GET /health (degraded mode)
- POST /api/reports/html (with cache enabled)
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, create_autospec

import pytest
from fastapi.testclient import TestClient

from api.app.data.db import Database
from api.app.domain.enums import PaymentMethod, ResolutionOutcome, RiskLevel
from api.app.llm.client import LLMResult
from api.app.main import app
from api.app.rag.embedder import FastEmbedder
from api.app.rag.retriever import QdrantRetriever
from api.app.rag.updater import RAGUpdater


@pytest.fixture
def test_client_routes(in_memory_db_path, mock_llm_blocker):
    """FastAPI test client for route-level tests."""
    db = Database(in_memory_db_path)

    mock_qdrant = MagicMock()
    # autospec: el doble respeta la FIRMA real de encode(). Con MagicMock pelado,
    # una llamada con un argumento que ya no existe pasa el test y falla en produccion.
    mock_embedder = create_autospec(FastEmbedder, instance=True)
    mock_embedder.encode.return_value = [[0.1] * 1024]

    from api.app.analysis.analyzer import Analyzer
    from api.app.reports.generator import ReportGenerator
    from api.app.services.feedback import FeedbackService
    from api.app.services.resolution import ResolutionService

    retriever = create_autospec(QdrantRetriever, instance=True)
    retriever.search_policies.return_value = []
    retriever.search_similar_cases.return_value = []

    analyzer = Analyzer(db)
    report_gen = ReportGenerator()
    mock_tracer = MagicMock()
    mock_tracer.trace.return_value = ""
    mock_updater = create_autospec(RAGUpdater, instance=True)
    mock_updater.on_case_resolved.return_value = True

    resolution_service = ResolutionService(mock_llm_blocker, mock_tracer)
    feedback_service = FeedbackService(db, mock_updater, mock_tracer)

    app.state.db = db
    app.state.qdrant = mock_qdrant
    app.state.llm = mock_llm_blocker
    app.state.retriever = retriever
    app.state.indexer = MagicMock()
    app.state.updater = mock_updater
    app.state.analyzer = analyzer
    app.state.tracer = mock_tracer
    app.state.report_generator = report_gen
    app.state.settings = MagicMock()
    app.state.settings.admin_api_key = ""
    app.state.settings.report_cache_enabled = True
    app.state.settings.qdrant_policies_collection = "policies"
    app.state.settings.qdrant_cases_collection = "historical_cases"

    mock_collection_info = MagicMock()
    mock_collection_info.points_count = 0
    mock_qdrant.get_collection.return_value = mock_collection_info
    app.state.embedder = mock_embedder
    app.state.resolution_service = resolution_service
    app.state.feedback_service = feedback_service
    app.state.pipeline_service = MagicMock()

    from api.app.observability.tracer import NoOpTracer
    from api.app.services.langfuse_stats import LangfuseStatsService

    # NoOpTracer y no un mock: la pregunta del test es "sin Langfuse detras", y un
    # MagicMock responde que si a cualquier capacidad que se le consulte.
    app.state.langfuse_stats_service = LangfuseStatsService(NoOpTracer(), "claude-sonnet-4-6")

    from api.app.llm.manager import LLMManager
    from api.app.services.modelos import ModelosService

    # Sin esto, `settings` es un MagicMock y todo campo nuevo sale truthy:
    # el modo demo se daria por configurado y armaria un cliente con mocks.
    app.state.settings.demo_mode = False
    app.state.settings.demo_provider = ""
    app.state.settings.demo_model = ""
    app.state.settings.llm_provider = "anthropic"
    app.state.settings.llm_model = "claude-haiku-4-5-20251001"
    app.state.settings.llm_model_resolution = "claude-sonnet-4-6"
    app.state.settings.llm_base_url = ""
    app.state.settings.llm_api_key = ""
    app.state.settings.anthropic_api_key = "test"
    db.ensure_modelos_table()
    app.state.modelos_service = ModelosService(
        db, app.state.settings, LLMManager(app.state.settings, mock_tracer),
    )

    # Ensure report cache table exists
    db.ensure_report_cache_table()

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, db, mock_updater


# ---- Transaction List ----

def test_list_transactions(test_client_routes):
    """GET /api/transactions should return list of all transactions."""
    client, _, _ = test_client_routes
    resp = client.get("/api/transactions")
    assert resp.status_code == 200
    data = resp.json()
    assert "transactions" in data
    assert len(data["transactions"]) == 2


# ---- Client History ----

def test_client_history_known(test_client_routes):
    """GET /api/clients/{id}/history with known client returns history."""
    client, _, _ = test_client_routes
    resp = client.get("/api/clients/CLI-0003/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["client_id"] == "CLI-0003"
    assert data["total_transactions"] >= 1


def test_client_history_not_found(test_client_routes):
    """GET /api/clients/{id}/history with unknown client returns 404."""
    client, _, _ = test_client_routes
    resp = client.get("/api/clients/CLI-NONEXISTENT/history")
    assert resp.status_code == 404


# ---- Merchant Risk ----

def test_merchant_risk_unknown(test_client_routes):
    """GET /api/merchants/{name}/risk with unknown merchant returns zero stats."""
    client, _, _ = test_client_routes
    resp = client.get("/api/merchants/UnknownCorp/risk")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_transactions"] == 0
    assert data["cb_ratio"] == 0.0


# ---- SLA Check Edge Cases ----

def test_sla_check_vip(test_client_routes):
    """POST /api/sla/check with VIP client should use 5-day SLA."""
    client, _, _ = test_client_routes
    resp = client.post("/api/sla/check", json={
        "case_open_date": date.today().isoformat(),
        "country": "ARG",
        "cliente_vip": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["sla_type"] == "vip"
    assert data["sla_limit_days"] == 5


def test_sla_check_non_latam(test_client_routes):
    """POST /api/sla/check with non-LATAM country should use 15-day SLA."""
    client, _, _ = test_client_routes
    resp = client.post("/api/sla/check", json={
        "case_open_date": date.today().isoformat(),
        "country": "USA",
        "cliente_vip": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["sla_type"] == "extended"
    assert data["sla_limit_days"] == 15


def test_sla_check_breached(test_client_routes):
    """POST /api/sla/check with old date should flag compensation_applicable."""
    client, _, _ = test_client_routes
    old_date = (date.today() - timedelta(days=30)).isoformat()
    resp = client.post("/api/sla/check", json={
        "case_open_date": old_date,
        "country": "MEX",
        "cliente_vip": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["within_sla"] is False
    assert data["compensation_applicable"] is True


# ---- Judge Low Score ----

def test_judge_low_score_not_approved(test_client_routes):
    """POST /api/analyze/judge with score < 7.0 should return approved=False."""
    client, _, _ = test_client_routes

    # Override LLM to return low score
    app.state.llm = MagicMock()
    app.state.llm.complete.return_value = LLMResult(
        text='{"overall_score":5.5,"criteria":{"policy_consistency":6.0,'
        '"justification_quality":5.0,"precedent_usage":5.0,'
        '"risk_assessment":6.0,"actionability":5.5},'
        '"strengths":[],"weaknesses":["Justificacion debil"]}',
        input_tokens=600, output_tokens=150,
    )
    mock_tracer = MagicMock()
    mock_tracer.trace.return_value = "trace-low"
    app.state.resolution_service = __import__(
        "api.app.services.resolution", fromlist=["ResolutionService"]
    ).ResolutionService(app.state.llm, mock_tracer)

    resp = client.post("/api/analyze/judge", json={
        "resolution": {
            "transaction_id": "TXN-00051",
            "recommended_action": ResolutionOutcome.REJECT,
            "risk_level": RiskLevel.HIGH,
            "confidence": 0.7,
            "justification": "Weak reason",
            "policy_verdicts": [],
            "precedent_summary": "",
            "log_summary": "",
            "next_steps": [],
        },
        "full_context": {"transaction": {"id": "TXN-00051"}},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_score"] == 5.5
    assert data["approved"] is False


# ---- Feedback With Resolution ----

def test_feedback_with_resolution_triggers_auto_index(test_client_routes):
    """POST /api/feedback with resolution and high score should trigger auto-indexing."""
    client, _, mock_updater = test_client_routes
    mock_updater.on_case_resolved.return_value = True

    resp = client.post("/api/feedback/", json={
        "transaction_id": "TXN-00051",
        "analyst_decision": "APPROVED",
        "analyst_notes": "Verified",
        "final_outcome": ResolutionOutcome.REJECT,
        "judge_score": 9.0,
        "resolution": {"justification": "BLOCKER cripto confirmed"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["auto_indexed"] is True
    mock_updater.on_case_resolved.assert_called_once()


# ---- Cache with Enabled ----

def test_cache_lookup_miss_when_enabled(test_client_routes):
    """GET /api/cache/lookup should return cached=False on miss even when enabled."""
    client, _, _ = test_client_routes
    resp = client.get("/api/cache/lookup", params={
        "transaction_id": "TXN-MISSING",
        "cliente_vip": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is False


def test_cache_lookup_hit_when_enabled(test_client_routes):
    """GET /api/cache/lookup should return cached=True when report is stored."""
    client, db, _ = test_client_routes
    db.store_cached_report("TXN-00051|False", "<html>Cached Report</html>")

    resp = client.get("/api/cache/lookup", params={
        "transaction_id": "TXN-00051",
        "cliente_vip": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cached"] is True
    assert "Cached Report" in data["html"]


# ---- Health Degraded ----

def test_health_qdrant_failure(test_client_routes):
    """GET /health should return degraded when Qdrant fails."""
    client, _, _ = test_client_routes
    app.state.qdrant.get_collection.side_effect = Exception("Connection refused")

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert "error" in data["qdrant"]

    # Reset for other tests
    app.state.qdrant.get_collection.side_effect = None


# ---- Logs with empty result ----

def test_logs_empty_returns_zero_count(test_client_routes):
    """GET /api/logs/{tx_id} should return log_count=0 when no logs exist."""
    client, _, _ = test_client_routes
    resp = client.get("/api/logs/TXN-00051")
    assert resp.status_code == 200
    data = resp.json()
    assert data["transaction_id"] == "TXN-00051"
    assert data["log_count"] == 0  # fixture has no logs
    assert data["logs"] == []


# ---- Report with auto-cache ----

def test_report_html_caches_when_enabled(test_client_routes):
    """POST /api/reports/html should auto-cache when report_cache_enabled=True."""
    client, db, _ = test_client_routes
    payload = {
        "transaction": {
            "id": "TXN-00051", "client_id": "CLI-0003", "merchant": "Airbnb",
            "amount_usd": 2095.90, "date": "2024-09-23", "payment_method": PaymentMethod.CRYPTO,
            "country": "COL", "channel": "POS", "device": "Firefox/Mac",
            "fraud_score": 8, "status": "Contracargo iniciado", "notes": None,
        },
        "resolution": {
            "transaction_id": "TXN-00051", "recommended_action": ResolutionOutcome.REJECT,
            "confidence": 0.99, "justification": "BLOCKER cripto",
            "policy_verdicts": [], "precedent_summary": "", "log_summary": "",
            "risk_level": RiskLevel.BLOCKER, "compensation_applicable": False,
            "compensation_amount_usd": 0.0, "next_steps": ["Notificar"],
            "requires_hitl": False, "hitl_reason": None,
        },
        "judge_evaluation": {
            "overall_score": 9.2,
            "criteria": {"policy_consistency": 10.0, "justification_quality": 9.0,
                         "precedent_usage": 8.0, "risk_assessment": 9.5, "actionability": 9.5},
            "approved": True, "strengths": ["OK"], "weaknesses": [],
        },
        "agent_analysis": "BLOCKER.",
        "merchant_risk": {"merchant": "Airbnb", "cb_ratio": 0.02, "total_transactions": 10,
                          "total_chargebacks": 2, "total_volume_usd": 5000,
                          "avg_transaction_usd": 500, "flags": [], "is_strategic": False},
        "client_profile": {"client_id": "CLI-0003", "total_transactions": 5,
                           "total_chargebacks": 1, "rejected_transactions": 0,
                           "countries_used": ["COL"], "payment_methods_used": [PaymentMethod.CRYPTO], "flags": []},
        "logs": [],
        "policies_evaluated": [],
        "similar_cases": [],
        "hitl_decision": None,
        "cache_hit": False,
        "guardrail_warnings": [],
    }
    resp = client.post("/api/reports/html", json=payload)
    assert resp.status_code == 200

    # Verify it was cached
    cached = db.get_cached_report("TXN-00051|False")
    assert cached is not None
    assert "TXN-00051" in cached


# ---- Langfuse Stats ----

def test_langfuse_stats_disabled(test_client_routes):
    """GET /api/langfuse/stats returns disabled when using mock tracer."""
    client, _, _ = test_client_routes
    resp = client.get("/api/langfuse/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["summary"] is None
    assert data["recent_traces"] == []


class TestConfiguracionDeModelos:
    """Elegir el modelo de cada paso es una peticion HTTP, no un deploy."""

    def test_devuelve_los_tres_pasos_y_el_catalogo(self, test_client_routes):
        cliente, _, _ = test_client_routes
        d = cliente.get("/api/config/modelos").json()
        assert set(d["pasos"]) == {"policy_eval", "resolution", "judge"}
        assert any(p["gratis"] for p in d["proveedores"]), "ningun proveedor con free tier"

    def test_guardar_cambia_solo_ese_paso(self, test_client_routes):
        cliente, _, _ = test_client_routes
        cliente.post("/api/config/modelos/reset")
        r = cliente.put("/api/config/modelos/judge",
                       json={"proveedor": "groq", "modelo": "llama-3.3-70b-versatile"})
        assert r.status_code == 200
        pasos = cliente.get("/api/config/modelos").json()["pasos"]
        assert pasos["judge"]["proveedor"] == "groq"
        assert pasos["policy_eval"]["personalizado"] is False
        cliente.post("/api/config/modelos/reset")

    def test_un_paso_inexistente_da_422(self, test_client_routes):
        cliente, _, _ = test_client_routes
        r = cliente.put("/api/config/modelos/inventado", json={"proveedor": "groq", "modelo": "x"})
        assert r.status_code == 422

    def test_un_modelo_vacio_da_422(self, test_client_routes):
        cliente, _, _ = test_client_routes
        r = cliente.put("/api/config/modelos/judge", json={"proveedor": "groq", "modelo": "   "})
        assert r.status_code == 422

    def test_reset_vuelve_al_default(self, test_client_routes):
        cliente, _, _ = test_client_routes
        cliente.put("/api/config/modelos/judge", json={"proveedor": "groq", "modelo": "x"})
        cliente.post("/api/config/modelos/reset")
        pasos = cliente.get("/api/config/modelos").json()["pasos"]
        assert not any(p["personalizado"] for p in pasos.values())

    def test_el_endpoint_no_acepta_credenciales(self):
        """Lo que se elige es *que* modelo, nunca *con que credencial*."""
        from api.app.domain.models import ModeloPasoUpdate

        campos = set(ModeloPasoUpdate.model_fields)
        assert campos == {"proveedor", "modelo"}
        assert not any("key" in c or "clave" in c or "token" in c for c in campos)

    def test_el_panel_trae_la_seccion_de_modelos(self, test_client_routes):
        cliente, _, _ = test_client_routes
        html = cliente.get("/panel").text
        assert "modelos-section" in html and "cargarModelos()" in html


class TestLosDosEndpointsRespetanElModo:
    """`/resolve` y `/judge` tienen que usar el MISMO modelo.

    Regresion: `judge` resolvia el servicio del modo demo y despues llamaba al
    de produccion. Con la cuenta de Anthropic sin credito eso era un 500, y
    desde n8n se veia como «Error en analisis LLM» sin decir que la mitad del
    pipeline se habia ido por el proveedor equivocado. Es la clase de bug que un
    test de una sola ruta no encuentra: cada una anda, y juntas no.
    """

    @staticmethod
    def _servicios_usados(monkeypatch):
        from unittest.mock import MagicMock

        import api.app.routes.analyze as analyze

        usados = []
        demo = MagicMock()
        demo.resolve.return_value = {"recommended_action": "APPROVE", "risk_level": "LOW",
                                     "confidence": 0.9, "policy_verdicts": []}
        demo.judge.return_value = {"overall_score": 8.0, "approved": True, "criteria": {}}

        def espia(settings, modelos, base):
            usados.append("demo")
            return demo, True

        monkeypatch.setattr(analyze, "_servicio_efectivo", espia)
        return usados, demo

    def test_judge_usa_el_servicio_del_modo_y_no_el_de_produccion(self, monkeypatch, test_client_routes):
        cliente, _, _ = test_client_routes
        _, demo = self._servicios_usados(monkeypatch)

        r = cliente.post("/api/analyze/judge", json={
            "resolution": {"transaction_id": "TXN-00051", "recommended_action": "REJECT"},
            "full_context": {"transaction": {"id": "TXN-00051"}},
        })
        assert r.status_code == 200
        assert demo.judge.called, "el juez corrio con el servicio de produccion"
        assert r.json()["demo"] is True, "una corrida en modo demo tiene que declararse"
