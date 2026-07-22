"""
Unit tests for the RAG retriever's query builder.
These tests are pure Python — no Qdrant required.
"""

import pytest
from api.app.rag.retriever import QueryBuilder


class TestQueryBuilder:

    def test_crypto_enrichment(self):
        """Query for Cripto transactions should include the non-reversible enrichment."""
        q = QueryBuilder.for_policies(
            motivo="No reconoce la compra",
            channel="POS",
            payment_method="Cripto",
            fraud_score=8,
            country="COL",
        )
        assert "criptomonedas" in q.lower(), "Cripto query must mention criptomonedas"
        assert "no reversible" in q.lower() or "irreversible" in q.lower() or "cripto" in q.lower()

    def test_low_score_enrichment(self):
        """Query for fraud_score < 30 should include high-risk enrichment."""
        q = QueryBuilder.for_policies(
            motivo=None,
            channel="Web",
            payment_method="Credito Visa",
            fraud_score=15,
            country="MEX",
        )
        assert "alto riesgo" in q.lower() or "fraude" in q.lower()

    def test_no_low_score_enrichment_above_threshold(self):
        """Query for fraud_score >= 30 should NOT include high-risk enrichment."""
        q = QueryBuilder.for_policies(
            motivo=None,
            channel="App Movil",
            payment_method="Debito Visa",
            fraud_score=75,
            country="ARG",
        )
        # Should not have low-score enrichment
        assert "alto riesgo fraude score bajo" not in q.lower()

    def test_non_latam_enrichment(self):
        """Query for non-LATAM country should include extended deadline enrichment."""
        q = QueryBuilder.for_policies(
            motivo="Cargo duplicado",
            channel="API",
            payment_method="Credito MC",
            fraud_score=50,
            country="USA",
        )
        assert "latam" in q.lower() or "internacional" in q.lower() or "plazo" in q.lower()

    def test_latam_no_international_enrichment(self):
        """Query for LATAM country should NOT include non-LATAM enrichment."""
        q = QueryBuilder.for_policies(
            motivo=None,
            channel="Web",
            payment_method="Cuenta Virtual",
            fraud_score=60,
            country="ARG",
        )
        # Should not have non-LATAM enrichment
        assert "fuera LATAM" not in q

    def test_ivr_enrichment(self):
        """Query for IVR channel should mention IVR limit."""
        q = QueryBuilder.for_policies(
            motivo=None,
            channel="IVR",
            payment_method="Debito MC",
            fraud_score=55,
            country="MEX",
        )
        assert "ivr" in q.lower()

    def test_similar_cases_query_structure(self):
        """Similar cases query should include merchant, amount, payment method."""
        q = QueryBuilder.for_similar_cases(
            merchant="Airbnb",
            amount=2095.90,
            payment_method="Cripto",
            country="COL",
            fraud_score=8,
            motivo="No reconoce la compra",
        )
        assert "Airbnb" in q
        assert "2095.90" in q
        assert "Cripto" in q
        assert "COL" in q
        assert "No reconoce la compra" in q

    def test_similar_cases_without_motivo(self):
        """Similar cases query should work without motivo."""
        q = QueryBuilder.for_similar_cases(
            merchant="Amazon",
            amount=150.00,
            payment_method="Credito Visa",
            country="MEX",
            fraud_score=70,
        )
        assert "Amazon" in q
        assert "150.00" in q
        assert "No reconoce" not in q  # motivo not included when None

    def test_multiple_enrichments_combined(self):
        """Cripto + low score + non-LATAM should all be enriched."""
        q = QueryBuilder.for_policies(
            motivo="No reconoce la compra",
            channel="POS",
            payment_method="Cripto",
            fraud_score=5,
            country="USA",
        )
        assert "cripto" in q.lower()
        assert "alto riesgo" in q.lower() or "fraude" in q.lower()
        assert "latam" in q.lower() or "internacional" in q.lower()
