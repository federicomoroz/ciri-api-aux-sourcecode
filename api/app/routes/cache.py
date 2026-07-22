"""
Idempotency cache: lookup cached HTML reports by (transaction_id, motivo, cliente_vip).

Prevents re-executing the entire LLM pipeline for duplicate or repeated requests.
Uses the existing Qdrant _semantic_cache collection.
"""

import logging

from fastapi import APIRouter, Depends

from ..config import Settings
from ..dependencies import get_retriever, get_settings
from ..domain.models import CacheLookupRequest
from ..rag.retriever import QdrantRetriever

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cache", tags=["cache"])


def _cache_key(transaction_id: str, motivo: str | None, cliente_vip: bool) -> str:
    return f"report:{transaction_id}|{motivo or ''}|{cliente_vip}"


@router.post("/lookup")
def cache_lookup(
    req: CacheLookupRequest,
    retriever: QdrantRetriever = Depends(get_retriever),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Check if a cached HTML report exists for this request."""
    if not settings.semantic_cache_enabled:
        return {"cached": False}

    key = _cache_key(req.transaction_id, req.motivo, req.cliente_vip)
    result = retriever.check_semantic_cache(key)

    if result and isinstance(result, dict) and "html" in result:
        logger.info("Report cache HIT for %s", req.transaction_id)
        return {"cached": True, "html": result["html"]}

    return {"cached": False}
