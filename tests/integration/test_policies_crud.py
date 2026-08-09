"""
Integration tests for the Policy CRUD API.
Requires: SQLite (in-memory via fixture). Qdrant mocked.
"""

from unittest.mock import MagicMock, create_autospec

import pytest
from fastapi.testclient import TestClient

from api.app.analysis.analyzer import Analyzer
from api.app.analysis.sla import CalculadoraDeSLA
from api.app.data import esquema
from api.app.data.db import Database
from api.app.main import app
from api.app.rag.embedder import FastEmbedder
from api.app.rag.indexer import QdrantIndexer
from api.app.rag.retriever import QdrantRetriever
from api.app.rag.updater import RAGUpdater
from api.app.reports.generator import ReportGenerator
from api.app.services.feedback import FeedbackService
from api.app.services.resolution import ResolutionService


@pytest.fixture
def test_client(in_memory_db_path):
    """FastAPI test client with mocked RAG components."""
    mock_qdrant = MagicMock()
    # autospec: el doble respeta la FIRMA real de encode(). Con MagicMock pelado,
    # una llamada con un argumento que ya no existe pasa el test y falla en produccion.
    mock_embedder = create_autospec(FastEmbedder, instance=True)
    mock_embedder.encode.return_value = [[0.1] * 1024]
    mock_llm = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.trace.return_value = ""

    db = Database(in_memory_db_path)
    indexer = create_autospec(QdrantIndexer, instance=True)
    retriever = create_autospec(QdrantRetriever, instance=True)
    retriever.search_policies.return_value = []
    updater = create_autospec(RAGUpdater, instance=True)
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
    app.state.sla = CalculadoraDeSLA(db)
    app.state.tracer = mock_tracer
    app.state.report_generator = create_autospec(ReportGenerator, instance=True)
    app.state.settings = MagicMock()
    app.state.settings.admin_api_key = ""
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


class TestTocarUnaPoliticaInvalidaLosInformes:
    """Editar una politica reindexaba Qdrant y no se veia en el informe.

    El cache tiene clave `(transaction_id, cliente_vip)` y ninguna nocion de que
    politicas regian cuando se genero. Reproducido contra el deploy: se creo una
    politica, `health` paso de 17 a 18 colecciones y la busqueda ya la devolvia;
    se volvio a pedir el mismo caso y salio `cache_hit: true` con el HTML
    anterior byte a byte.

    La decision de arquitectura numero dos del proyecto —las politicas son
    datos, editarlas no requiere deploy— era cierta en el indice y **no se veia**
    donde el usuario mira. El ejemplo del propio README no funcionaba si el caso
    ya se habia corrido.
    """

    @staticmethod
    def _db(tmp_path):
        from api.app.data.db import Database

        db = Database(str(tmp_path / "c.db"))
        esquema.preparar(db)
        return db

    def test_el_cache_se_puede_vaciar(self, tmp_path):
        db = self._db(tmp_path)
        db.store_cached_report("TXN-00051|False", "<html>viejo</html>")
        assert db.clear_report_cache() == 1
        assert db.get_cached_report("TXN-00051|False") is None

    def test_vaciar_un_cache_vacio_no_falla(self, tmp_path):
        assert self._db(tmp_path).clear_report_cache() == 0

    def test_se_vacia_entero_y_no_por_caso(self, tmp_path):
        """Una politica aplica a cualquier transaccion: no se sabe cuales quedaron viejos."""
        db = self._db(tmp_path)
        for txn in ("TXN-00001", "TXN-00051", "TXN-00089"):
            db.store_cached_report(f"{txn}|False", "<html>x</html>")
        assert db.clear_report_cache() == 3

    def test_las_tres_rutas_de_escritura_lo_invalidan(self):
        """Crear, editar y borrar dejan viejos los informes por igual."""
        import re
        from pathlib import Path

        codigo = Path("api/app/routes/policies.py").read_text(encoding="utf-8")
        for verbo in ("post", "put", "delete"):
            cuerpo = re.split(r"@router\.", codigo)
            handler = next(c for c in cuerpo if c.startswith(f'{verbo}('))
            assert "_invalidar_informes" in handler, f"{verbo.upper()} no invalida el cache"
