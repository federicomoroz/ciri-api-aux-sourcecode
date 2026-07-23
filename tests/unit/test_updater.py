"""
Unit tests for RAGUpdater — Axis 6 auto-improvement loop.

Covers:
- on_case_resolved(): auto-indexing when judge_score >= threshold
- on_case_resolved(): skipping when judge_score < threshold
- on_case_resolved(): skipping when transaction not found
- Policy CRUD delegation to indexer
"""

from unittest.mock import MagicMock

import pytest

from api.app.domain.constants import JUDGE_AUTO_INDEX_THRESHOLD
from api.app.rag.updater import RAGUpdater


@pytest.fixture
def mock_indexer():
    return MagicMock()


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_transaction.return_value = {
        "id": "TXN-00051",
        "merchant": "Airbnb",
        "amount_usd": 2095.90,
        "payment_method": "Cripto",
        "country": "COL",
        "fraud_score": 8,
    }
    return db


@pytest.fixture
def updater(mock_indexer, mock_db):
    return RAGUpdater(mock_indexer, mock_db)


class TestOnCaseResolved:
    """Axis 6: auto-indexing of high-quality resolved cases as new precedents."""

    def test_indexes_case_when_score_above_threshold(self, updater, mock_indexer, mock_db):
        """Judge score >= 8.0 should trigger indexing."""
        case = {"case_id": "FB-42", "transaction_id": "TXN-00051", "motivo": "Cripto"}
        result = updater.on_case_resolved(case, judge_score=9.0)
        assert result is True
        mock_db.get_transaction.assert_called_once_with("TXN-00051")
        mock_indexer.index_single_case.assert_called_once_with(case, mock_db.get_transaction.return_value)

    def test_indexes_case_at_exact_threshold(self, updater, mock_indexer):
        """Judge score exactly at threshold should trigger indexing."""
        case = {"case_id": "FB-43", "transaction_id": "TXN-00051", "motivo": "Test"}
        result = updater.on_case_resolved(case, judge_score=JUDGE_AUTO_INDEX_THRESHOLD)
        assert result is True
        mock_indexer.index_single_case.assert_called_once()

    def test_skips_indexing_when_score_below_threshold(self, updater, mock_indexer, mock_db):
        """Judge score < 8.0 should NOT trigger indexing."""
        case = {"case_id": "FB-44", "transaction_id": "TXN-00051", "motivo": "Low quality"}
        result = updater.on_case_resolved(case, judge_score=7.5)
        assert result is False
        mock_db.get_transaction.assert_not_called()
        mock_indexer.index_single_case.assert_not_called()

    def test_skips_indexing_when_transaction_not_found(self, updater, mock_indexer, mock_db):
        """Missing transaction should prevent indexing."""
        mock_db.get_transaction.return_value = None
        case = {"case_id": "FB-45", "transaction_id": "TXN-MISSING", "motivo": "Unknown"}
        result = updater.on_case_resolved(case, judge_score=9.5)
        assert result is False
        mock_indexer.index_single_case.assert_not_called()

    def test_returns_false_on_indexing_error(self, updater, mock_indexer):
        """Indexing failure should return False, not raise."""
        mock_indexer.index_single_case.side_effect = Exception("Qdrant down")
        case = {"case_id": "FB-46", "transaction_id": "TXN-00051", "motivo": "Error case"}
        result = updater.on_case_resolved(case, judge_score=9.0)
        assert result is False


class TestPolicyOperations:
    """Policy CRUD operations delegate to indexer."""

    def test_on_policy_created(self, updater, mock_indexer):
        policy = {"code": "POL-TEST-001", "name": "Test", "category": "FRAUDE",
                  "description": "Test policy", "reference": "Test"}
        updater.on_policy_created(policy)
        mock_indexer.index_single_policy.assert_called_once_with(policy)

    def test_on_policy_updated(self, updater, mock_indexer):
        policy = {"code": "POL-FRD-001", "name": "Updated", "category": "FRAUDE",
                  "description": "Updated description", "reference": "Test"}
        updater.on_policy_updated(policy)
        mock_indexer.delete_policy.assert_called_once_with("POL-FRD-001")
        mock_indexer.index_single_policy.assert_called_once_with(policy)

    def test_on_policy_deleted(self, updater, mock_indexer):
        updater.on_policy_deleted("POL-FRD-001")
        mock_indexer.delete_policy.assert_called_once_with("POL-FRD-001")
