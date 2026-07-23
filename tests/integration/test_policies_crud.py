"""
Integration tests for the Policy CRUD API.
Requires: SQLite (in-memory via fixture). Qdrant mocked.
"""

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.data.db import Database


@pytest.fixture
def test_client(in_memory_db_path):
    """FastAPI test client with mocked RAG components."""
    mock_qdrant = MagicMock()
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = [[0.1] * 1024]
    mock_llm = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.trace.return_value = ""
    mock_report_gen = MagicMock()

    from api.app.rag.indexer import QdrantIndexer
    from api.app.rag.retriever import QdrantRetriever
    from api.app.rag.updater import RAGUpdater
    from api.app.analysis.analyzer import Analyzer
    from api.app.reports.generator import ReportGenerator
    from api.app.services.resolution import ResolutionService
    from api.app.services.feedback import FeedbackService

    db = Database(in_memory_db_path)
    indexer = MagicMock()
    retriever = MagicMock()
    retriever.search_policies.return_value = []
    updater = MagicMock()
    analyzer = Analyzer(db)

    resolution_service = ResolutionService(mock_llm, mock_tracer)
    feedback_service = FeedbackService(db, updater, mock_tracer)

    app.state.db = db
    app.state.qdrant = mock_qdrant
    app.state.llm = mock_llm
    app.state.retriever = retriever
    app.state.indexer = indexer
    app.state.updater = updater
    app.state.analyzer = analyzer
    app.state.tracer = mock_tracer
    app.state.report_generator = MagicMock()
    app.state.settings = MagicMock()
    app.state.embedder = mock_embedder
    app.state.resolution_service = resolution_service
    app.state.feedback_service = feedback_service
    app.state.pipeline_service = MagicMock()

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, updater


def test_list_policies(test_client):
    """GET /api/policies should return list of policies."""
    client, _ = test_client
    resp = client.get("/api/policies/")
    assert resp.status_code == 200
    policies = resp.json()
    assert isinstance(policies, list)
    assert len(policies) > 0


def test_get_policy_by_code(test_client):
    """GET /api/policies/{code} should return specific policy."""
    client, _ = test_client
    resp = client.get("/api/policies/POL-FRD-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "POL-FRD-001"
    assert "description" in data


def test_get_policy_not_found(test_client):
    """GET /api/policies/NONEXISTENT should return 404."""
    client, _ = test_client
    resp = client.get("/api/policies/POL-XXX-999")
    assert resp.status_code == 404


def test_create_policy(test_client):
    """POST /api/policies should create policy in SQLite + trigger Qdrant indexing."""
    client, updater = test_client
    new_policy = {
        "code": "POL-TEST-001",
        "name": "Politica de prueba",
        "category": "FRAUDE",
        "description": "Esta es una politica de prueba para tests automatizados.",
        "reference": "Test Reference v1.0",
    }
    resp = client.post("/api/policies/", json=new_policy)
    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "POL-TEST-001"
    # Verify Qdrant indexing was triggered
    updater.on_policy_created.assert_called_once()


def test_update_policy(test_client):
    """PUT /api/policies/{code} should update + re-index in Qdrant."""
    client, updater = test_client
    update_data = {"description": "Descripcion actualizada para prueba de re-indexacion."}
    resp = client.put("/api/policies/POL-FRD-001", json=update_data)
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "Descripcion actualizada para prueba de re-indexacion."
    # Verify Qdrant re-indexing was triggered
    updater.on_policy_updated.assert_called_once()


def test_delete_policy(test_client):
    """DELETE /api/policies/{code} should remove from SQLite + Qdrant."""
    client, updater = test_client
    # First create a policy to delete
    new_policy = {
        "code": "POL-DEL-001",
        "name": "Policy to delete",
        "category": "SLA",
        "description": "Will be deleted in test.",
        "reference": "Test",
    }
    client.post("/api/policies/", json=new_policy)
    updater.reset_mock()

    resp = client.delete("/api/policies/POL-DEL-001")
    assert resp.status_code == 204
    # Verify Qdrant deletion was triggered
    updater.on_policy_deleted.assert_called_once_with("POL-DEL-001")
    # Verify it's gone from SQLite
    get_resp = client.get("/api/policies/POL-DEL-001")
    assert get_resp.status_code == 404
