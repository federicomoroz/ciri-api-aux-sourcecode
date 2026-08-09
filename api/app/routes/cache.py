"""
Idempotency cache: lookup cached HTML reports by (transaction_id, cliente_vip).

Uses SQLite exact-match — zero Voyage AI calls, zero latency overhead.
"""

import logging

from fastapi import APIRouter, Depends, Query

from ..config import Settings
from ..data.db import Database, cache_key
from ..dependencies import get_db, get_settings
from ..domain.models import CacheLookupResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cache", tags=["cache"])


@router.get("/lookup")
def cache_lookup(
    transaction_id: str = Query(...),
    cliente_vip: bool = Query(False),
    motivo: str = Query("", description="Parte de la clave: cambia el analisis entero"),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CacheLookupResponse:
    """Check if a cached HTML report exists for this exact request.

    El `motivo` entra en la clave porque decide que politicas y que precedentes
    recupera el RAG: dos reclamos distintos sobre la misma transaccion son dos
    analisis distintos. Es opcional para no romper a quien ya llama sin el, pero
    el orquestador lo manda.
    """
    if not settings.report_cache_enabled:
        return {"cached": False}

    key = cache_key(transaction_id, cliente_vip=cliente_vip, motivo=motivo)
    html = db.get_cached_report(key)

    if html:
        logger.info("Report cache HIT for %s", transaction_id)
        return {"cached": True, "html": html}

    logger.info("Report cache MISS for %s", transaction_id)
    return {"cached": False}
