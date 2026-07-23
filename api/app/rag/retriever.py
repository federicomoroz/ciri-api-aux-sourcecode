"""
RAG retriever with deterministic query builder.

Design decisions:
- QueryBuilder is deterministic (no LLM). Reproducible, zero cost, faster.
- policies: retrieve ALL 17 (small corpus). LLM filters. threshold=0.0
- historical_cases: top-5, threshold=0.40 (semantic similarity)
- _semantic_cache: threshold=0.92 (near-identical queries only)
"""

import logging

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from .embedder import FastEmbedder

from ..domain.constants import (
    FRAUD_SCORE_DEFAULT,
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    LATAM_COUNTRIES,
    POLICIES_TOP_K,
    POLICIES_SCORE_THRESHOLD,
    RERANK_COUNTRY_BOOST,
    RERANK_MAX_SCORE,
    RERANK_PAYMENT_METHOD_BOOST,
    SIMILAR_CASES_TOP_K,
    SIMILAR_CASES_SCORE_THRESHOLD,
)
from ..domain.enums import Channel, PaymentMethod

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
        cache_collection: str = "_semantic_cache",
    ):
        self.client = client
        self.embedder = embedder
        self.policies_collection = policies_collection
        self.cases_collection = cases_collection
        self.cache_collection = cache_collection

    def _embed(self, text: str) -> list[float]:
        return self.embedder.encode([text], show_progress_bar=False)[0].tolist()

    def search_policies(
        self,
        motivo: str | None = None,
        channel: str = "",
        payment_method: str = "",
        fraud_score: int = FRAUD_SCORE_DEFAULT,
        country: str = "",
        top_k: int = POLICIES_TOP_K,
        score_threshold: float = POLICIES_SCORE_THRESHOLD,
    ) -> list[dict]:
        """Semantic search over policies collection.
        Returns ALL policies (small corpus; LLM will filter relevance)."""
        query = QueryBuilder.for_policies(motivo, channel, payment_method, fraud_score, country)
        vector = self._embed(query)

        try:
            results = self.client.query_points(
                collection_name=self.policies_collection,
                query=vector,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            ).points
        except Exception as e:
            logger.error("Qdrant policy search failed: %s", e)
            raise

        return [
            {**r.payload, "score": round(r.score, 4), "_query": query}
            for r in results
        ]

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
        vector = self._embed(query)

        # Soft filter: prefer results with same payment_method (should = boost, not exclude)
        query_filter = Filter(
            should=[
                FieldCondition(key="payment_method", match=MatchValue(value=payment_method)),
            ]
        )

        try:
            results = self.client.query_points(
                collection_name=self.cases_collection,
                query=vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            ).points
        except Exception as e:
            logger.error("Qdrant case search failed: %s", e)
            raise

        results = self._rerank(results, payment_method, country)

        return [
            {**r.payload, "score": round(r.score, 4), "_query": query}
            for r in results
        ]

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
