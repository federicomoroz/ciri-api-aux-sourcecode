"""
Unit tests for QdrantIndexer — Qdrant operations with mocked client.

Covers:
- Policy indexing (batch + single)
- Case indexing (batch + single)
- Deletion operations
- Helper functions (_make_id, _policy_to_markdown, _case_to_text)
"""

from unittest.mock import MagicMock, call

import numpy as np
import pytest

from api.app.rag.indexer import (
    QdrantIndexer,
    _case_to_text,
    _make_id,
    _policy_to_markdown,
)


# ---- Helpers ----

class TestMakeId:
    def test_deterministic(self):
        """Same input should always produce same UUID."""
        assert _make_id("POL-FRD-001") == _make_id("POL-FRD-001")

    def test_different_inputs_different_ids(self):
        """Different inputs should produce different UUIDs."""
        assert _make_id("POL-FRD-001") != _make_id("POL-FRD-002")

    def test_uuid_format(self):
        """Output should be a valid UUID string (8-4-4-4-12)."""
        result = _make_id("test")
        assert len(result) == 36
        assert result.count("-") == 4


class TestPolicyToMarkdown:
    def test_contains_all_fields(self):
        policy = {
            "code": "POL-FRD-001",
            "category": "FRAUDE",
            "name": "Score minimo",
            "description": "Score < 30 = rechazo.",
            "reference": "Manual v3.2",
        }
        md = _policy_to_markdown(policy)
        assert "POL-FRD-001" in md
        assert "FRAUDE" in md
        assert "Score minimo" in md
        assert "Score < 30 = rechazo." in md
        assert "Manual v3.2" in md

    def test_markdown_structure(self):
        policy = {
            "code": "POL-TEST-001",
            "category": "SLA",
            "name": "Test",
            "description": "Desc",
            "reference": "Ref",
        }
        md = _policy_to_markdown(policy)
        assert md.startswith("# POL-TEST-001")
        assert "**Categoria:**" in md
        assert "## Descripcion" in md


class TestCaseToText:
    def test_with_transaction(self):
        case = {
            "case_id": "CB-001",
            "motivo": "Fraude",
            "resolution": "A favor del cliente",
            "observations": "Caso claro",
        }
        tx = {
            "merchant": "Amazon",
            "payment_method": "Credito Visa",
            "country": "MEX",
            "amount_usd": 150.0,
            "fraud_score": 25,
        }
        text = _case_to_text(case, tx)
        assert "CB-001" in text
        assert "Fraude" in text
        assert "Amazon" in text
        assert "150.00" in text
        assert "Caso claro" in text

    def test_without_transaction(self):
        case = {
            "case_id": "CB-002",
            "motivo": "Cargo duplicado",
            "resolution": "Reembolso",
        }
        text = _case_to_text(case, None)
        assert "CB-002" in text
        assert "Cargo duplicado" in text
        assert "Reembolso" in text

    def test_without_observations(self):
        case = {
            "case_id": "CB-003",
            "motivo": "Test",
            "resolution": "Pendiente",
        }
        text = _case_to_text(case, None)
        assert "Observaciones" not in text


# ---- QdrantIndexer ----

@pytest.fixture
def mock_qdrant():
    return MagicMock()


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.encode.return_value = [np.array([0.1] * 1024), np.array([0.2] * 1024), np.array([0.3] * 1024)]
    return embedder


@pytest.fixture
def indexer(mock_qdrant, mock_embedder):
    return QdrantIndexer(mock_qdrant, mock_embedder)


class TestIndexPolicies:
    def test_indexes_all_policies(self, indexer, mock_qdrant, mock_embedder):
        policies = [
            {"code": "POL-001", "name": "P1", "category": "FRAUDE", "description": "D1", "reference": "R1"},
            {"code": "POL-002", "name": "P2", "category": "SLA", "description": "D2", "reference": "R2"},
        ]
        mock_embedder.encode.return_value = [np.array([0.1] * 1024), np.array([0.2] * 1024)]
        count = indexer.index_policies(policies)
        assert count == 2
        mock_qdrant.upsert.assert_called_once()

    def test_empty_policies_no_upsert(self, indexer, mock_qdrant, mock_embedder):
        mock_embedder.encode.return_value = np.array([])
        count = indexer.index_policies([])
        assert count == 0
        mock_qdrant.upsert.assert_not_called()

    def test_upsert_error_raises(self, indexer, mock_qdrant, mock_embedder):
        mock_embedder.encode.return_value = [np.array([0.1] * 1024)]
        mock_qdrant.upsert.side_effect = Exception("Qdrant connection refused")
        with pytest.raises(Exception, match="Qdrant connection refused"):
            indexer.index_policies([
                {"code": "POL-001", "name": "P1", "category": "FRAUDE", "description": "D1", "reference": "R1"},
            ])


class TestIndexSinglePolicy:
    def test_indexes_one_policy(self, indexer, mock_qdrant, mock_embedder):
        mock_embedder.encode.return_value = [np.array([0.1] * 1024)]
        policy = {"code": "POL-NEW-001", "name": "New", "category": "SLA", "description": "Desc", "reference": "Ref"}
        indexer.index_single_policy(policy)
        mock_qdrant.upsert.assert_called_once()
        point = mock_qdrant.upsert.call_args[1]["points"][0]
        assert point.payload["code"] == "POL-NEW-001"

    def test_error_raises(self, indexer, mock_qdrant, mock_embedder):
        mock_embedder.encode.return_value = [np.array([0.1] * 1024)]
        mock_qdrant.upsert.side_effect = Exception("timeout")
        with pytest.raises(Exception, match="timeout"):
            indexer.index_single_policy(
                {"code": "X", "name": "X", "category": "X", "description": "X", "reference": "X"}
            )


class TestDeletePolicy:
    def test_deletes_by_code(self, indexer, mock_qdrant):
        indexer.delete_policy("POL-FRD-001")
        mock_qdrant.delete.assert_called_once()
        args = mock_qdrant.delete.call_args[1]
        assert args["collection_name"] == "policies"

    def test_error_raises(self, indexer, mock_qdrant):
        mock_qdrant.delete.side_effect = Exception("not found")
        with pytest.raises(Exception, match="not found"):
            indexer.delete_policy("POL-MISSING")


class TestIndexSingleCase:
    def test_indexes_case_with_tx(self, indexer, mock_qdrant, mock_embedder):
        mock_embedder.encode.return_value = [np.array([0.1] * 1024)]
        case = {"case_id": "CB-001", "motivo": "Fraude", "resolution": "Rechazo"}
        tx = {"merchant": "Airbnb", "amount_usd": 500.0, "payment_method": "Cripto", "country": "COL", "fraud_score": 8}
        indexer.index_single_case(case, tx)
        mock_qdrant.upsert.assert_called_once()
        point = mock_qdrant.upsert.call_args[1]["points"][0]
        assert point.payload["merchant"] == "Airbnb"
        assert point.payload["payment_method"] == "Cripto"


class TestIndexHistoricalCases:
    def test_indexes_all_cases(self, indexer, mock_qdrant, mock_embedder):
        mock_embedder.encode.return_value = [np.array([0.1] * 1024), np.array([0.2] * 1024)]
        cases = [
            {"case_id": "CB-001", "transaction_id": "TXN-001", "motivo": "Fraude", "resolution": "Rechazo"},
            {"case_id": "CB-002", "transaction_id": "TXN-002", "motivo": "Cargo doble", "resolution": "Reembolso"},
        ]
        txns = [
            {"id": "TXN-001", "merchant": "Amazon", "amount_usd": 100, "payment_method": "Visa", "country": "MEX", "fraud_score": 50},
            {"id": "TXN-002", "merchant": "Uber", "amount_usd": 20, "payment_method": "Debito", "country": "ARG", "fraud_score": 80},
        ]
        count = indexer.index_historical_cases(cases, txns)
        assert count == 2
        mock_qdrant.upsert.assert_called_once()

    def test_case_without_matching_tx(self, indexer, mock_qdrant, mock_embedder):
        """Case with no matching transaction should still index with empty merchant/country."""
        mock_embedder.encode.return_value = [np.array([0.1] * 1024)]
        cases = [{"case_id": "CB-ORPHAN", "transaction_id": "TXN-MISSING", "motivo": "Unknown", "resolution": "N/A"}]
        count = indexer.index_historical_cases(cases, [])
        assert count == 1
        point = mock_qdrant.upsert.call_args[1]["points"][0]
        assert point.payload["merchant"] == ""
