"""
Idempotency cache: lookup cached HTML reports by (transaction_id, motivo, cliente_vip).

Uses SQLite exact-match — zero Voyage AI calls, zero latency overhead.
"""

import logging

from fastapi import APIRouter, Depends

from ..config import Settings
from ..data.db import Database
from ..dependencies import get_db, get_settings
from ..domain.models import CacheLookupRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cache", tags=["cache"])


def _cache_key(transaction_id: str, motivo: str | None = None, cliente_vip: bool = False) -> str:
    return f"{transaction_id}|{cliente_vip}"


@router.post("/lookup")
def cache_lookup(
    req: CacheLookupRequest,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Check if a cached HTML report exists for this exact request."""
    if not settings.semantic_cache_enabled:
        return {"cached": False}

    key = _cache_key(req.transaction_id, req.motivo, req.cliente_vip)
    html = db.get_cached_report(key)

    if html:
        logger.info("Report cache HIT for %s", req.transaction_id)
        return {"cached": True, "html": html}

    return {"cached": False}
