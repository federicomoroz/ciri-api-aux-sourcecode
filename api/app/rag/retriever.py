"""
RAG retriever with deterministic query builder.

Design decisions:
- QueryBuilder is deterministic (no LLM). Reproducible, zero cost, faster.
- policies: retrieve ALL (small corpus, counted per query). LLM filters. threshold=0.0
- historical_cases: 15 candidatos, rerank por boosts, top-5. threshold=0.40
"""

import logging

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from ..domain.constants import (
    FRAUD_SCORE_DEFAULT,
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    LATAM_COUNTRIES,
    POLICIES_SCORE_THRESHOLD,
    POLICIES_TOP_K_FALLBACK,
    RERANK_COUNTRY_BOOST,
    RERANK_MAX_SCORE,
    RERANK_PAYMENT_METHOD_BOOST,
    SIMILAR_CASES_CANDIDATOS,
    SIMILAR_CASES_SCORE_THRESHOLD,
    SIMILAR_CASES_TOP_K,
)
from ..domain.enums import Channel, PaymentMethod
from .embedder import FastEmbedder

logger = logging.getLogger(__name__)


class QueryBuilder:
    """Builds Qdrant search queries without using an LLM.
    Contextual enrichment based on transaction fields."""

    @staticmethod
    def for_policies(
        motivo: str | None,
        channel: str,
        payment_method: str,
        fraud_score: int,
        country: str,
    ) -> str:
        """Build policy search query with contextual enrichment."""
        base = f"contracargo {motivo or ''}, {channel}, {payment_method}, score {fraud_score}/100, {country}"
        parts = [base]
        if payment_method == PaymentMethod.CRYPTO:
            parts.append("criptomonedas no reversible blocker")
        if fraud_score < FRAUD_SCORE_HIGH_RISK_THRESHOLD:
            parts.append("transaccion de alto riesgo fraude score bajo")
        if country not in LATAM_COUNTRIES:
            parts.append("internacional fuera LATAM plazo extendido")
        if channel == Channel.IVR:
            parts.append("limite monto IVR")
        return " ".join(parts)

    @staticmethod
    def for_similar_cases(
        merchant: str,
        amount: float,
        payment_method: str,
        country: str,
        fraud_score: int,
        motivo: str | None = None,
    ) -> str:
        """Build case similarity query."""
        q = f"Contracargo en {merchant} por USD {amount:.2f}, {payment_method}, {country}, score {fraud_score}"
        if motivo:
            q += f", motivo: {motivo}"
        return q


class QdrantRetriever:
    def __init__(
        self,
        client: QdrantClient,
        embedder: FastEmbedder,
        policies_collection: str = "policies",
        cases_collection: str = "historical_cases",
    ):
        self.client = client
        self.embedder = embedder
        self.policies_collection = policies_collection
        self.cases_collection = cases_collection

    def _embed(self, text: str) -> list[float]:
        return self.embedder.encode([text])[0].tolist()

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single Voyage AI API call."""
        vectors = self.embedder.encode(texts)
        return [v.tolist() for v in vectors]

    def _total_politicas(self) -> int:
        """Cuantas politicas hay indexadas ahora mismo.

        El limite de recuperacion era la constante 17 — las del dataset. Cargar
        la politica 18 por la API la indexaba bien, pero la peor rankeada de las
        18 desaparecia del contexto del LLM sin log ni aviso. El corpus es chico
        y contar es barato: se pregunta en vez de asumir.
        """
        try:
            total = self.client.count(self.policies_collection, exact=True).count
            return max(1, total)
        except Exception as e:
            logger.warning(
                "No se pudo contar %s (%s): se recupera hasta %d politicas",
                self.policies_collection, e, POLICIES_TOP_K_FALLBACK,
            )
            return POLICIES_TOP_K_FALLBACK

    def _query_policies(
        self,
        vector: list[float],
        query: str,
        top_k: int | None = None,
        score_threshold: float = POLICIES_SCORE_THRESHOLD,
    ) -> list[dict]:
        """Busca politicas con un vector ya calculado."""
        try:
            results = self.client.query_points(
                collection_name=self.policies_collection,
                query=vector,
                limit=top_k or self._total_politicas(),
                score_threshold=score_threshold,
                with_payload=True,
            ).points
        except Exception as e:
            logger.error("Qdrant policy search failed: %s", e)
            raise
        return [{**r.payload, "score": round(r.score, 4), "_query": query} for r in results]

    def _query_cases(
        self,
        vector: list[float],
        query: str,
        payment_method: str,
        country: str,
        top_k: int = SIMILAR_CASES_TOP_K,
        score_threshold: float = SIMILAR_CASES_SCORE_THRESHOLD,
    ) -> list[dict]:
        """Busca precedentes con un vector ya calculado, filtra suave y reordena."""
        # Filtro blando: prefiere el mismo metodo de pago, no lo exige.
        query_filter = Filter(
            should=[FieldCondition(key="payment_method", match=MatchValue(value=payment_method))]
        )
        # Se piden mas candidatos de los que se van a entregar: el rerank tiene
        # que poder cambiar QUIEN entra, no solo el orden de los que ya entraron.
        candidatos = max(top_k, SIMILAR_CASES_CANDIDATOS)
        try:
            results = self.client.query_points(
                collection_name=self.cases_collection,
                query=vector,
                query_filter=query_filter,
                limit=candidatos,
                score_threshold=score_threshold,
                with_payload=True,
            ).points
        except Exception as e:
            logger.error("Qdrant case search failed: %s", e)
            raise
        results = self._rerank(results, payment_method, country)[:top_k]
        return [{**r.payload, "score": round(r.score, 4), "_query": query} for r in results]

    def search_policies(
        self,
        motivo: str | None = None,
        channel: str = "",
        payment_method: str = "",
        fraud_score: int = FRAUD_SCORE_DEFAULT,
        country: str = "",
        top_k: int | None = None,
        score_threshold: float = POLICIES_SCORE_THRESHOLD,
    ) -> list[dict]:
        """Semantic search over policies collection.

        Sin `top_k` devuelve TODAS las politicas indexadas, sean 17 o 200: el
        corpus es chico y el LLM filtra relevancia."""
        query = QueryBuilder.for_policies(motivo, channel, payment_method, fraud_score, country)
        return self._query_policies(self._embed(query), query, top_k, score_threshold)

    def search_similar_cases(
        self,
        merchant: str,
        amount: float,
        payment_method: str,
        country: str,
        fraud_score: int,
        motivo: str | None = None,
        top_k: int = SIMILAR_CASES_TOP_K,
        score_threshold: float = SIMILAR_CASES_SCORE_THRESHOLD,
    ) -> list[dict]:
        """Hybrid search: vector similarity + metadata filtering + reranking."""
        query = QueryBuilder.for_similar_cases(
            merchant, amount, payment_method, country, fraud_score, motivo
        )
        return self._query_cases(
            self._embed(query), query, payment_method, country, top_k, score_threshold,
        )

    def search_policies_and_cases(
        self,
        *,
        motivo: str | None = None,
        channel: str = "",
        payment_method: str = "",
        fraud_score: int = FRAUD_SCORE_DEFAULT,
        country: str = "",
        merchant: str = "",
        amount: float = 0.0,
    ) -> tuple[list[dict], list[dict]]:
        """Las dos busquedas con una sola llamada a Voyage.

        Unica diferencia con llamar a los dos buscadores por separado: ahorra un
        viaje de ida y vuelta a la API de embeddings. La busqueda en si es la
        misma, para que no puedan divergir.
        """
        policy_query = QueryBuilder.for_policies(
            motivo, channel, payment_method, fraud_score, country,
        )
        case_query = QueryBuilder.for_similar_cases(
            merchant, amount, payment_method, country, fraud_score, motivo,
        )
        policy_vec, case_vec = self._embed_batch([policy_query, case_query])

        return (
            self._query_policies(policy_vec, policy_query),
            self._query_cases(case_vec, case_query, payment_method, country),
        )

    @staticmethod
    def _rerank(results: list, payment_method: str, country: str) -> list:
        """Boost results sharing payment_method or country with the query transaction."""
        for r in results:
            boost = 0.0
            if r.payload.get("payment_method") == payment_method:
                boost += RERANK_PAYMENT_METHOD_BOOST
            if r.payload.get("country") == country:
                boost += RERANK_COUNTRY_BOOST
            r.score = min(r.score + boost, RERANK_MAX_SCORE)
        return sorted(results, key=lambda r: r.score, reverse=True)
