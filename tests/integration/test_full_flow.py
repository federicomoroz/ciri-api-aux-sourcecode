"""
Integration tests for the full analysis flow.
Uses MockLLMClient + real SQLite + mocked Qdrant.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.data.db import Database


@pytest.fixture
def test_client_full_flow(in_memory_db_path, mock_llm_blocker):
    """FastAPI test client for full flow tests."""
    db = Database(in_memory_db_path)

    mock_qdrant = MagicMock()
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = [[0.1] * 384]

    from api.app.rag.retriever import QdrantRetriever
    from api.app.analysis.analyzer import Analyzer
    from api.app.reports.generator import ReportGenerator
    from api.app.services.resolution import ResolutionService
    from api.app.services.feedback import FeedbackService

    # Mock retriever to return pre-set results
    retriever = MagicMock()
    retriever.search_policies.return_value = [
        {
            "code": "POL-EXC-003",
            "name": "Transacciones con Criptomonedas",
            "category": "EXCEPCION",
            "description": "BLOCKER: no se aceptan contracargos con cripto.",
            "reference": "Manual de Excepciones v1.0, Sec. 4",
            "score": 0.98,
        },
        {
            "code": "POL-FRD-001",
            "name": "Score minimo de aprobacion",
            "category": "FRAUDE",
            "description": "Score < 30 debe ser rechazado.",
            "reference": "Manual de Riesgo v3.2, Sec. 4.1",
            "score": 0.92,
        },
    ]
    retriever.search_similar_cases.return_value = []
    retriever.format_policies_for_prompt.return_value = "POL-EXC-003: BLOCKER cripto\nPOL-FRD-001: Score bajo"
    retriever.format_cases_for_prompt.return_value = "(Sin precedentes)"
    retriever.check_semantic_cache.return_value = None

    analyzer = Analyzer(db)
    report_gen = ReportGenerator()
    mock_tracer = MagicMock()
    mock_tracer.trace.return_value = ""
    mock_updater = MagicMock()
    mock_updater.on_case_resolved.return_value = False
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
    app.state.settings.semantic_cache_enabled = False
    app.state.embedder = mock_embedder
    app.state.resolution_service = resolution_service
    app.state.feedback_service = feedback_service

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_get_transaction(test_client_full_flow):
    """GET /api/transactions/{id} should return transaction data."""
    client = test_client_full_flow
    resp = client.get("/api/transactions/TXN-00051")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "TXN-00051"
    assert data["payment_method"] == "Cripto"
    assert data["fraud_score"] == 8


def test_get_transaction_not_found(test_client_full_flow):
    """GET /api/transactions/INVALID should return 404."""
    client = test_client_full_flow
    resp = client.get("/api/transactions/TXN-99999")
    assert resp.status_code == 404


def test_get_logs(test_client_full_flow):
    """GET /api/logs/{tx_id} should return log data."""
    client = test_client_full_flow
    resp = client.get("/api/logs/TXN-00051")
    assert resp.status_code == 200
    data = resp.json()
    assert "transaction_id" in data
    assert "log_count" in data
    assert "severity_summary" in data


def test_find_similar_cases(test_client_full_flow):
    """GET /api/cases/similar should return semantic search results."""
    client = test_client_full_flow
    resp = client.get("/api/cases/similar", params={
        "merchant": "Airbnb",
        "amount": 2095.90,
        "payment_method": "Cripto",
        "country": "COL",
        "fraud_score": 8,
        "motivo": "No reconoce la compra",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert "count" in data


def test_merchant_risk(test_client_full_flow):
    """GET /api/merchants/{name}/risk should return risk profile."""
    client = test_client_full_flow
    resp = client.get("/api/merchants/Airbnb/risk")
    assert resp.status_code == 200
    data = resp.json()
    assert "cb_ratio" in data
    assert "total_volume_usd" in data


def test_sla_check(test_client_full_flow):
    """POST /api/sla/check should return SLA status."""
    client = test_client_full_flow
    resp = client.post("/api/sla/check", json={
        "case_open_date": "2024-09-23",
        "country": "COL",
        "cliente_vip": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "within_sla" in data
    assert "sla_limit_days" in data
    assert data["sla_limit_days"] == 10  # COL is LATAM → standard


def test_resolve_blocker_scenario(test_client_full_flow):
    """POST /api/analyze/resolve with Cripto tx should return REJECT + BLOCKER."""
    client = test_client_full_flow
    payload = {
        "transaction_id": "TXN-00051",
        "agent_analysis": "Cripto con score 8 — BLOCKER",
        "tx_data": {
            "id": "TXN-00051", "payment_method": "Cripto",
            "fraud_score": 8, "amount_usd": 2095.90,
            "country": "COL", "merchant": "Airbnb", "channel": "POS",
        },
        "policies": [
            {"code": "POL-EXC-003", "description": "BLOCKER cripto", "category": "EXCEPCION", "name": "Cripto", "reference": ""},
        ],
        "similar_cases": [],
        "logs": [],
        "merchant_risk": {"cb_ratio": 0.02, "flags": ["high_cb_ratio"]},
        "client_history": {"total_chargebacks": 1, "flags": []},
        "motivo": "No reconoce la compra",
        "cliente_vip": False,
    }
    resp = client.post("/api/analyze/resolve", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["recommended_action"] == "REJECT"
    assert data["risk_level"] == "BLOCKER"


def test_judge_evaluation(test_client_full_flow):
    """POST /api/analyze/judge should return a score between 1 and 10."""
    client = test_client_full_flow
    payload = {
        "resolution": {
            "transaction_id": "TXN-00051",
            "recommended_action": "REJECT",
            "risk_level": "BLOCKER",
            "confidence": 0.99,
            "justification": "BLOCKER por POL-EXC-003",
            "policy_verdicts": [],
            "precedent_summary": "",
            "log_summary": "",
            "next_steps": [],
        },
        "full_context": {"transaction": {"id": "TXN-00051", "payment_method": "Cripto"}},
    }
    resp = client.post("/api/analyze/judge", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "overall_score" in data
    assert 1.0 <= data["overall_score"] <= 10.0
    assert "approved" in data


def test_submit_feedback(test_client_full_flow):
    """POST /api/feedback should record feedback and attempt auto-indexing."""
    client = test_client_full_flow
    payload = {
        "transaction_id": "TXN-00051",
        "analyst_decision": "APPROVED",
        "analyst_notes": "Verificado manualmente. Caso correcto.",
        "final_outcome": "REJECT",
        "judge_score": 9.2,
        "resolution": None,
    }
    resp = client.post("/api/feedback", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert "feedback_id" in data


def test_html_report_generation(test_client_full_flow):
    """POST /api/reports/html should return valid HTML."""
    client = test_client_full_flow
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
            "policy_verdicts": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto", "requires_human_review": False}],
            "precedent_summary": "", "log_summary": "", "risk_level": "BLOCKER",
            "compensation_applicable": False, "compensation_amount_usd": 0.0,
            "next_steps": ["Notificar al cliente"], "requires_hitl": False, "hitl_reason": None,
        },
        "judge_evaluation": {
            "overall_score": 9.2,
            "criteria": {"policy_consistency": 10.0, "justification_quality": 9.0, "precedent_usage": 8.0, "risk_assessment": 9.5, "actionability": 9.5},
            "approved": True, "strengths": ["Correcto"], "weaknesses": [],
        },
        "agent_analysis": "BLOCKER detectado.",
        "merchant_risk": {"merchant": "Airbnb", "cb_ratio": 0.02, "total_transactions": 10, "total_chargebacks": 2, "total_volume_usd": 5000, "avg_transaction_usd": 500, "flags": [], "is_strategic": False},
        "client_profile": {"client_id": "CLI-0003", "total_transactions": 5, "total_chargebacks": 1, "rejected_transactions": 0, "countries_used": ["COL"], "payment_methods_used": ["Cripto"], "flags": []},
        "logs": [],
        "policies_evaluated": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto", "requires_human_review": False}],
        "similar_cases": [],
        "hitl_decision": None,
        "cache_hit": False,
        "guardrail_warnings": [],
    }
    resp = client.post("/api/reports/html", json=payload)
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    html = data["html"]
    assert "TXN-00051" in html
    assert "BLOCKER" in html
    assert "Airbnb" in html
