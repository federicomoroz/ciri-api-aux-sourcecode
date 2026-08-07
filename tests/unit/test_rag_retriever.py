"""
Unit tests for the RAG retriever's query builder and reranking.
These tests are pure Python — no Qdrant required.
"""

from unittest.mock import MagicMock

import pytest

from api.app.domain.constants import POLICIES_TOP_K_FALLBACK, SIMILAR_CASES_TOP_K
from api.app.domain.enums import PaymentMethod
from api.app.rag.retriever import QdrantRetriever, QueryBuilder


class TestQueryBuilder:

    def test_crypto_enrichment(self):
        """Query for Cripto transactions should include the non-reversible enrichment."""
        q = QueryBuilder.for_policies(
            motivo="No reconoce la compra",
            channel="POS",
            payment_method=PaymentMethod.CRYPTO,
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
            payment_method=PaymentMethod.CREDIT_VISA,
            fraud_score=15,
            country="MEX",
        )
        assert "alto riesgo" in q.lower() or "fraude" in q.lower()

    def test_no_low_score_enrichment_above_threshold(self):
        """Query for fraud_score >= 30 should NOT include high-risk enrichment."""
        q = QueryBuilder.for_policies(
            motivo=None,
            channel="App Movil",
            payment_method=PaymentMethod.DEBIT_VISA,
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
            payment_method=PaymentMethod.CREDIT_MC,
            fraud_score=50,
            country="USA",
        )
        assert "latam" in q.lower() or "internacional" in q.lower() or "plazo" in q.lower()

    def test_latam_no_international_enrichment(self):
        """Query for LATAM country should NOT include non-LATAM enrichment."""
        q = QueryBuilder.for_policies(
            motivo=None,
            channel="Web",
            payment_method=PaymentMethod.VIRTUAL_ACCOUNT,
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
            payment_method=PaymentMethod.DEBIT_MC,
            fraud_score=55,
            country="MEX",
        )
        assert "ivr" in q.lower()

    def test_similar_cases_query_structure(self):
        """Similar cases query should include merchant, amount, payment method."""
        q = QueryBuilder.for_similar_cases(
            merchant="Airbnb",
            amount=2095.90,
            payment_method=PaymentMethod.CRYPTO,
            country="COL",
            fraud_score=8,
            motivo="No reconoce la compra",
        )
        assert "Airbnb" in q
        assert "2095.90" in q
        assert PaymentMethod.CRYPTO in q
        assert "COL" in q
        assert "No reconoce la compra" in q

    def test_similar_cases_without_motivo(self):
        """Similar cases query should work without motivo."""
        q = QueryBuilder.for_similar_cases(
            merchant="Amazon",
            amount=150.00,
            payment_method=PaymentMethod.CREDIT_VISA,
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
            payment_method=PaymentMethod.CRYPTO,
            fraud_score=5,
            country="USA",
        )
        assert "cripto" in q.lower()
        assert "alto riesgo" in q.lower() or "fraude" in q.lower()
        assert "latam" in q.lower() or "internacional" in q.lower()


class TestReranking:
    """Tests for the _rerank() method — score boosting by metadata match."""

    def _make_result(self, score: float, payment_method: str, country: str):
        """Create a mock Qdrant result with score and payload."""
        r = MagicMock()
        r.score = score
        r.payload = {"payment_method": payment_method, "country": country}
        return r

    def test_rerank_boosts_matching_payment_method(self):
        results = [
            self._make_result(0.80, PaymentMethod.CREDIT_VISA, "ARG"),
            self._make_result(0.78, PaymentMethod.CRYPTO, "MEX"),
        ]
        reranked = QdrantRetriever._rerank(results, PaymentMethod.CRYPTO, "COL")
        # Cripto match gets +0.05 boost → 0.83, should be first
        assert reranked[0].payload["payment_method"] == PaymentMethod.CRYPTO
        assert reranked[0].score == pytest.approx(0.83, abs=0.01)

    def test_rerank_boosts_matching_country(self):
        results = [
            self._make_result(0.75, PaymentMethod.CREDIT_VISA, "COL"),
            self._make_result(0.76, PaymentMethod.DEBIT_MC, "ARG"),
        ]
        reranked = QdrantRetriever._rerank(results, PaymentMethod.DEBIT_VISA, "COL")
        # COL match gets +0.03 → 0.78, should be first
        assert reranked[0].payload["country"] == "COL"

    def test_rerank_both_match_highest_boost(self):
        results = [
            self._make_result(0.70, PaymentMethod.CRYPTO, "COL"),
            self._make_result(0.77, PaymentMethod.CREDIT_VISA, "ARG"),
        ]
        reranked = QdrantRetriever._rerank(results, PaymentMethod.CRYPTO, "COL")
        # Cripto+COL = +0.08 → 0.78, beats 0.77
        assert reranked[0].payload["payment_method"] == PaymentMethod.CRYPTO

    def test_rerank_caps_at_one(self):
        results = [self._make_result(0.99, PaymentMethod.CRYPTO, "COL")]
        reranked = QdrantRetriever._rerank(results, PaymentMethod.CRYPTO, "COL")
        assert reranked[0].score == 1.0


class TestLimiteDeRecuperacionDePoliticas:
    """El corpus se recupera entero, tenga las 17 del dataset o las que le carguen.

    Regresion: el limite era la constante 17. Cargar la politica 18 por
    `POST /api/policies/` la indexaba bien y la listaba bien, pero la peor
    rankeada de las 18 quedaba fuera del contexto del LLM sin log ni aviso.
    """

    @staticmethod
    def _retriever(total: int | None = None, falla: bool = False):
        client = MagicMock()
        if falla:
            client.count.side_effect = RuntimeError("Qdrant no responde")
        else:
            client.count.return_value = MagicMock(count=total)
        client.query_points.return_value = MagicMock(points=[])
        embedder = MagicMock()
        embedder.encode.return_value = [MagicMock(tolist=lambda: [0.0] * 1024)]
        return QdrantRetriever(client, embedder), client

    def test_recupera_las_18_cuando_hay_18(self):
        retriever, client = self._retriever(total=18)
        retriever.search_policies(motivo="fraude")
        assert client.query_points.call_args.kwargs["limit"] == 18

    def test_recupera_las_200_cuando_hay_200(self):
        retriever, client = self._retriever(total=200)
        retriever.search_policies(motivo="fraude")
        assert client.query_points.call_args.kwargs["limit"] == 200

    def test_un_top_k_explicito_manda_sobre_el_conteo(self):
        retriever, client = self._retriever(total=200)
        retriever.search_policies(motivo="fraude", top_k=5)
        assert client.query_points.call_args.kwargs["limit"] == 5
        client.count.assert_not_called()

    def test_si_no_se_puede_contar_usa_el_limite_por_defecto(self):
        retriever, client = self._retriever(falla=True)
        retriever.search_policies(motivo="fraude")
        assert client.query_points.call_args.kwargs["limit"] == POLICIES_TOP_K_FALLBACK

    def test_una_coleccion_vacia_no_pide_limite_cero(self):
        """Qdrant rechaza limit=0; el conteo no puede traducirse literal."""
        retriever, client = self._retriever(total=0)
        retriever.search_policies(motivo="fraude")
        assert client.query_points.call_args.kwargs["limit"] >= 1


class TestElRerankPuedeCambiarQuienEntra:
    """Reordenar el top-5 no es rerankear.

    Regresión: los boosts se aplicaban después del `limit` de Qdrant, así que
    sólo cambiaban el orden de los cinco que ya habían entrado por coseno. Un
    precedente del mismo método de pago que salía sexto se perdía aunque el
    boost lo hubiera puesto primero.
    """

    @staticmethod
    def _caso(score: float, metodo: str, pais: str, case_id: str):
        r = MagicMock()
        r.score = score
        r.payload = {"case_id": case_id, "payment_method": metodo, "country": pais}
        return r

    def _retriever(self, casos):
        client = MagicMock()
        client.query_points.return_value = MagicMock(points=casos)
        embedder = MagicMock()
        embedder.encode.return_value = [MagicMock(tolist=lambda: [0.0] * 1024)]
        return QdrantRetriever(client, embedder), client

    def test_pide_mas_candidatos_de_los_que_entrega(self):
        retriever, client = self._retriever([])
        retriever.search_similar_cases("Airbnb", 100.0, PaymentMethod.CRYPTO, "COL", 8)
        assert client.query_points.call_args.kwargs["limit"] > SIMILAR_CASES_TOP_K

    def test_entrega_solo_el_top_k(self):
        casos = [self._caso(0.9 - i / 100, PaymentMethod.CREDIT_VISA, "MEX", f"CB-{i}") for i in range(15)]
        retriever, _ = self._retriever(casos)
        assert len(retriever.search_similar_cases("X", 1.0, PaymentMethod.CRYPTO, "COL", 8)) == SIMILAR_CASES_TOP_K

    def test_un_septimo_con_boost_desplaza_a_un_quinto_sin_boost(self):
        """Lo que antes era imposible: el boost cambia la composición del set."""
        casos = [self._caso(0.80 - i / 100, PaymentMethod.CREDIT_VISA, "MEX", f"CB-{i}") for i in range(6)]
        casos.append(self._caso(0.74, PaymentMethod.CRYPTO, "COL", "CB-BOOST"))
        retriever, _ = self._retriever(casos)
        entregados = retriever.search_similar_cases("X", 1.0, PaymentMethod.CRYPTO, "COL", 8)
        ids = [c["case_id"] for c in entregados]
        assert "CB-BOOST" in ids, "el boost no alcanzó a meterlo en el set entregado"
        assert ids[0] == "CB-BOOST", "0.74 + 0.08 supera al mejor de 0.80"
