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

import pytest
from unittest.mock import MagicMock
from datetime import date, timedelta
from fastapi.testclient import TestClient

from api.app.llm.client import LLMResult
from api.app.main import app
from api.app.data.db import Database


@pytest.fixture
def test_client_routes(in_memory_db_path, mock_llm_blocker):
    """FastAPI test client for route-level tests."""
    db = Database(in_memory_db_path)

    mock_qdrant = MagicMock()
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = [[0.1] * 1024]

    from api.app.analysis.analyzer import Analyzer
    from api.app.reports.generator import ReportGenerator
    from api.app.services.resolution import ResolutionService
    from api.app.services.feedback import FeedbackService

    retriever = MagicMock()
    retriever.search_policies.return_value = []
    retriever.search_similar_cases.return_value = []

    analyzer = Analyzer(db)
    report_gen = ReportGenerator()
    mock_tracer = MagicMock()
    mock_tracer.trace.return_value = ""
    mock_updater = MagicMock()
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
    app.state.settings.semantic_cache_enabled = True
    app.state.settings.qdrant_policies_collection = "policies"
    app.state.settings.qdrant_cases_collection = "historical_cases"
    app.state.settings.qdrant_cache_collection = "_semantic_cache"

    mock_collection_info = MagicMock()
    mock_collection_info.points_count = 0
    mock_qdrant.get_collection.return_value = mock_collection_info
    app.state.embedder = mock_embedder
    app.state.resolution_service = resolution_service
    app.state.feedback_service = feedback_service
    app.state.pipeline_service = MagicMock()

    from api.app.services.langfuse_stats import LangfuseStatsService
    app.state.langfuse_stats_service = LangfuseStatsService(mock_tracer, "claude-sonnet-4-6")

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
            "recommended_action": "REJECT",
            "risk_level": "HIGH",
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
        "final_outcome": "REJECT",
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
    """POST /api/reports/html should auto-cache when semantic_cache_enabled=True."""
    client, db, _ = test_client_routes
    payload = {
        "transaction": {
            "id": "TXN-00051", "client_id": "CLI-0003", "merchant": "Airbnb",
            "amount_usd": 2095.90, "date": "2024-09-23", "payment_method": "Cripto",
            "country": "COL", "channel": "POS", "device": "Firefox/Mac",
            "fraud_score": 8, "status": "Contracargo iniciado", "notes": None,
        },
        "resolution": {
            "transaction_id": "TXN-00051", "recommended_action": "REJECT",
            "confidence": 0.99, "justification": "BLOCKER cripto",
            "policy_verdicts": [], "precedent_summary": "", "log_summary": "",
            "risk_level": "BLOCKER", "compensation_applicable": False,
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
                           "countries_used": ["COL"], "payment_methods_used": ["Cripto"], "flags": []},
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
