import logging

from fastapi import APIRouter, Depends
from qdrant_client import QdrantClient

from ..config import Settings
from ..data.db import Database
from ..dependencies import get_db, get_qdrant, get_settings
from ..domain.constants import HEALTH_DEGRADED, HEALTH_HEALTHY, HEALTH_OK
from ..domain.models import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health(
    db: Database = Depends(get_db),
    qdrant: QdrantClient = Depends(get_qdrant),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Health check: verify SQLite and Qdrant connectivity."""
    sqlite_status = HEALTH_OK
    qdrant_status = HEALTH_OK
    collections = {}

    try:
        db.get_all_policies()
    except Exception as e:
        sqlite_status = f"error: {type(e).__name__}: {e}"
        logger.error("SQLite health check failed: %s", e)

    try:
        for name in [
            settings.qdrant_policies_collection,
            settings.qdrant_cases_collection,
            settings.qdrant_cache_collection,
        ]:
            info = qdrant.get_collection(name)
            collections[name] = info.points_count
    except Exception as e:
        qdrant_status = f"error: {type(e).__name__}: {e}"
        logger.error("Qdrant health check failed: %s", e)

    overall = HEALTH_HEALTHY if sqlite_status == HEALTH_OK and qdrant_status == HEALTH_OK else HEALTH_DEGRADED

    return {
        "status": overall,
        "sqlite": sqlite_status,
        "qdrant": qdrant_status,
        "collections": collections,
    }
