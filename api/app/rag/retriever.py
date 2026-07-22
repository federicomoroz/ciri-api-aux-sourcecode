"""
RAG retriever with deterministic query builder.

Design decisions:
- QueryBuilder is deterministic (no LLM). Reproducible, zero cost, faster.
- policies: retrieve ALL 17 (small corpus). LLM filters. threshold=0.0
- historical_cases: top-5, threshold=0.40 (semantic similarity)
- _semantic_cache: threshold=0.92 (near-identical queries only)
"""

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from .embedder import FastEmbedder

from ..domain.constants import (
    FRAUD_SCORE_DEFAULT,
    LATAM_COUNTRIES,
    POLICIES_TOP_K,
    POLICIES_SCORE_THRESHOLD,
    SIMILAR_CASES_TOP_K,
    SIMILAR_CASES_SCORE_THRESHOLD,
    SEMANTIC_CACHE_THRESHOLD,
)
from ..domain.enums import Channel, PaymentMethod
from .formatter import format_cases_for_prompt, format_policies_for_prompt

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
        if fraud_score < 30:
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
        self.query_builder = QueryBuilder()

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
        query = self.query_builder.for_policies(motivo, channel, payment_method, fraud_score, country)
        vector = self._embed(query)

        results = self.client.search(
            collection_name=self.policies_collection,
            query_vector=vector,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

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
        """Semantic search over historical_cases collection."""
        query = self.query_builder.for_similar_cases(
            merchant, amount, payment_method, country, fraud_score, motivo
        )
        vector = self._embed(query)

        results = self.client.search(
            collection_name=self.cases_collection,
            query_vector=vector,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

        return [
            {**r.payload, "score": round(r.score, 4), "_query": query}
            for r in results
        ]

    def check_semantic_cache(self, query: str, threshold: float = SEMANTIC_CACHE_THRESHOLD) -> dict | None:
        """Search cache for near-identical queries. Returns cached response or None."""
        try:
            vector = self._embed(query)
            results = self.client.search(
                collection_name=self.cache_collection,
                query_vector=vector,
                limit=1,
                score_threshold=threshold,
                with_payload=True,
            )
            if results:
                return results[0].payload.get("response")
        except Exception as e:
            logger.warning("Semantic cache lookup failed: %s", e)
        return None

    def store_in_cache(self, query: str, response: dict) -> None:
        """Store a query+response in the semantic cache."""
        try:
            vector = self._embed(query)
            point_id = str(uuid.uuid4())
            self.client.upsert(
                collection_name=self.cache_collection,
                points=[PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={"query": query, "response": response},
                )],
            )
        except Exception as e:
            logger.warning("Semantic cache store failed: %s", e)

    def format_policies_for_prompt(self, policies: list[dict]) -> str:
        """Delegate to module-level formatter."""
        return format_policies_for_prompt(policies)

    def format_cases_for_prompt(self, cases: list[dict]) -> str:
        """Delegate to module-level formatter."""
        return format_cases_for_prompt(cases)
