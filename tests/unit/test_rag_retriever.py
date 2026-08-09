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


class TestUnaTransaccionNoEsPrecedenteDeSiMisma:
    """El filtro que impide que el agente se lea la respuesta antes de opinar.

    Los casos de la propia transaccion analizada salian primeros y no por
    casualidad: el documento indexado lleva su merchant, su metodo, su pais y su
    monto formateado igual que la consulta, asi que el parecido es maximo por
    construccion, y encima el rerank les da los dos boosts —esos campos se
    copiaron de la misma transaccion, no pueden no coincidir—. En TXN-00006 eso
    puso a CB-0025 y CB-0047, sus dos casos propios, en los puestos 1 y 2, y la
    justificacion cito «CB-0025 fue aprobado en 10d» como tendencia de terceros.

    Se afirma sobre el `query_filter` que se le manda a Qdrant y no sobre los
    resultados: el mock devuelve lo mismo pase lo que pase, asi que filtrar mal
    era indetectable. Es la razon por la que el defecto vivio hasta hoy.
    """

    def _retriever(self):
        client = MagicMock()
        client.query_points.return_value = MagicMock(points=[])
        embedder = MagicMock()
        # Dos vectores: la busqueda combinada embebe politicas y casos de una.
        embedder.encode.return_value = [MagicMock(tolist=lambda: [0.0] * 1024)] * 2
        return QdrantRetriever(client, embedder), client

    @staticmethod
    def _excluidos(client) -> list[str]:
        """Los transaction_id que el filtro enviado a Qdrant deja afuera."""
        f = client.query_points.call_args.kwargs["query_filter"]
        return [
            c.match.value for c in (f.must_not or [])
            if getattr(c, "key", "") == "transaction_id"
        ]

    def test_excluye_los_casos_de_la_transaccion_analizada(self):
        retriever, client = self._retriever()
        retriever.search_similar_cases(
            "MercadoLibre", 4671.09, PaymentMethod.CREDIT_VISA, "CHL", 46,
            excluir_transaction_id="TXN-00006",
        )
        assert self._excluidos(client) == ["TXN-00006"]

    def test_la_busqueda_combinada_tambien_excluye(self):
        """El pipeline real pasa por aca, no por `search_similar_cases`."""
        retriever, client = self._retriever()
        retriever.search_policies_and_cases(
            motivo="No reconoce la compra", payment_method=PaymentMethod.CREDIT_VISA,
            country="CHL", merchant="MercadoLibre", amount=4671.09,
            excluir_transaction_id="TXN-00006",
        )
        assert self._excluidos(client) == ["TXN-00006"]

    def test_sin_transaccion_que_excluir_no_se_filtra_nada(self):
        """Un `must_not` con el id vacio excluiria los casos sin transaccion."""
        retriever, client = self._retriever()
        retriever.search_similar_cases(
            "MercadoLibre", 4671.09, PaymentMethod.CREDIT_VISA, "CHL", 46,
        )
        f = client.query_points.call_args.kwargs["query_filter"]
        assert not f.must_not

    def test_el_metodo_de_pago_no_excluye_a_nadie(self):
        """La preferencia por el mismo metodo la aplica el rerank, no el filtro.

        Habia un `should` por `payment_method` puesto para «preferir sin
        exigir», y en Qdrant un filtro que solo tiene `should` las vuelve
        obligatorias: era una igualdad exacta de string disfrazada de
        preferencia. Un metodo escrito distinto dejaba el informe sin ningun
        precedente. La preferencia sigue viva en `_rerank`, que suma un boost.
        """
        retriever, client = self._retriever()
        retriever.search_similar_cases(
            "MercadoLibre", 4671.09, PaymentMethod.CREDIT_VISA, "CHL", 46,
            excluir_transaction_id="TXN-00006",
        )
        f = client.query_points.call_args.kwargs["query_filter"]
        assert not f.should, "un `should` solitario es un filtro duro, no una preferencia"
        assert not f.must, "nada mas puede exigirse: el unico filtro duro es la autoexclusion"
