"""
Qdrant indexer for policies and historical cases.

Collections:
- policies: 17+ policy documents (Markdown, dynamic via CRUD API)
- historical_cases: 60+ case documents (auto-grows when Judge score >= threshold)
- _semantic_cache: cached analysis results

Point IDs: deterministic uuid5 from document code/id to allow upserts.
"""

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams
from ..domain.constants import EMBEDDING_DIM
from .embedder import FastEmbedder

logger = logging.getLogger(__name__)


def _make_id(text: str) -> str:
    """Deterministic UUID from text (for upsertable point IDs)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, text))


def _policy_to_markdown(policy: dict) -> str:
    """Convert a policy dict to a Markdown document for embedding."""
    return (
        f"# {policy['code']}\n"
        f"**Categoria:** {policy['category']}\n"
        f"**Politica:** {policy['name']}\n"
        f"## Descripcion\n"
        f"{policy['description']}\n"
        f"**Referencia:** {policy['reference']}"
    )


def _case_to_text(case: dict, tx: dict | None) -> str:
    """Convert a case + its transaction to searchable text."""
    parts = [
        f"Caso {case['case_id']}: contracargo por {case['motivo']}",
        f"Resolucion: {case['resolution']}",
    ]
    if tx:
        parts.extend([
            f"en {tx['merchant']}, {tx['payment_method']}, {tx['country']},",
            f"USD {tx['amount_usd']:.2f}, score antifraude {tx['fraud_score']}",
        ])
    if case.get("observations"):
        parts.append(f"Observaciones: {case['observations']}")
    return " ".join(parts)


class QdrantIndexer:
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

    def ensure_collections(self) -> None:
        """Create (or recreate if dim changed) the 3 Qdrant collections."""
        for name in [self.policies_collection, self.cases_collection, self.cache_collection]:
            try:
                if self.client.collection_exists(name):
                    info = self.client.get_collection(name)
                    existing_dim = info.config.params.vectors.size
                    if existing_dim != EMBEDDING_DIM:
                        logger.info("Collection %s dim mismatch (%d != %d), recreating", name, existing_dim, EMBEDDING_DIM)
                        self.client.delete_collection(name)
                    else:
                        continue
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
                )
                logger.info("Created Qdrant collection: %s (dim=%d)", name, EMBEDDING_DIM)
            except Exception as e:
                logger.error("Failed to ensure Qdrant collection %s: %s", name, e)
                raise

    def index_policies(self, policies: list[dict]) -> int:
        """Index all policies as Markdown documents. Returns count indexed."""
        points = []
        texts = [_policy_to_markdown(p) for p in policies]
        vectors = self.embedder.encode(texts, show_progress_bar=False)

        for policy, vector in zip(policies, vectors):
            points.append(PointStruct(
                id=_make_id(policy["code"]),
                vector=vector.tolist(),
                payload={
                    "code": policy["code"],
                    "name": policy["name"],
                    "category": policy["category"],
                    "description": policy["description"],
                    "reference": policy["reference"],
                    "markdown": _policy_to_markdown(policy),
                },
            ))

        if points:
            try:
                self.client.upsert(collection_name=self.policies_collection, points=points)
                logger.info("Indexed %d policies in Qdrant", len(points))
            except Exception as e:
                logger.error("Failed to index policies in Qdrant: %s", e)
                raise
        return len(points)

    def index_historical_cases(
        self, cases: list[dict], transactions: list[dict]
    ) -> int:
        """Index cases with transaction context. Returns count indexed."""
        tx_map = {t["id"]: t for t in transactions}
        points = []
        texts = [_case_to_text(c, tx_map.get(c["transaction_id"])) for c in cases]
        vectors = self.embedder.encode(texts, show_progress_bar=False)

        for case, text, vector in zip(cases, texts, vectors):
            tx = tx_map.get(case["transaction_id"], {})
            points.append(PointStruct(
                id=_make_id(case["case_id"]),
                vector=vector.tolist(),
                payload={
                    **case,
                    "merchant": tx.get("merchant", ""),
                    "amount_usd": tx.get("amount_usd", 0),
                    "payment_method": tx.get("payment_method", ""),
                    "country": tx.get("country", ""),
                    "fraud_score": tx.get("fraud_score", 0),
                    "_text": text,
                },
            ))

        if points:
            try:
                self.client.upsert(collection_name=self.cases_collection, points=points)
                logger.info("Indexed %d historical cases in Qdrant", len(points))
            except Exception as e:
                logger.error("Failed to index cases in Qdrant: %s", e)
                raise
        return len(points)

    def index_single_case(self, case: dict, tx: dict) -> None:
        """Index one newly resolved case as a precedent."""
        text = _case_to_text(case, tx)
        vector = self.embedder.encode([text], show_progress_bar=False)[0]
        point = PointStruct(
            id=_make_id(case["case_id"]),
            vector=vector.tolist(),
            payload={
                **case,
                "merchant": tx.get("merchant", ""),
                "amount_usd": tx.get("amount_usd", 0),
                "payment_method": tx.get("payment_method", ""),
                "country": tx.get("country", ""),
                "fraud_score": tx.get("fraud_score", 0),
                "_text": text,
            },
        )
        try:
            self.client.upsert(collection_name=self.cases_collection, points=[point])
            logger.info("Indexed single case %s in Qdrant", case["case_id"])
        except Exception as e:
            logger.error("Failed to index case %s: %s", case["case_id"], e)
            raise

    def index_single_policy(self, policy: dict) -> None:
        """Index or re-index one policy."""
        text = _policy_to_markdown(policy)
        vector = self.embedder.encode([text], show_progress_bar=False)[0]
        point = PointStruct(
            id=_make_id(policy["code"]),
            vector=vector.tolist(),
            payload={
                "code": policy["code"],
                "name": policy["name"],
                "category": policy["category"],
                "description": policy["description"],
                "reference": policy["reference"],
                "markdown": text,
            },
        )
        try:
            self.client.upsert(collection_name=self.policies_collection, points=[point])
            logger.info("Indexed policy %s in Qdrant", policy["code"])
        except Exception as e:
            logger.error("Failed to index policy %s: %s", policy["code"], e)
            raise

    def delete_policy(self, code: str) -> None:
        """Remove a policy point from Qdrant."""
        try:
            self.client.delete(
                collection_name=self.policies_collection,
                points_selector=PointIdsList(points=[_make_id(code)]),
            )
            logger.info("Deleted policy %s from Qdrant", code)
        except Exception as e:
            logger.error("Failed to delete policy %s from Qdrant: %s", code, e)
            raise
