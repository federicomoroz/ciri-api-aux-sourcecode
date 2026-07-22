from fastapi import APIRouter, Depends

from ..data.db import Database
from ..dependencies import get_db, get_qdrant
from qdrant_client import QdrantClient

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Database = Depends(get_db), qdrant: QdrantClient = Depends(get_qdrant)) -> dict:
    """Health check: verify SQLite and Qdrant connectivity."""
    sqlite_status = "ok"
    qdrant_status = "ok"
    collections = {}

    try:
        db.get_all_policies()
    except Exception as e:
        sqlite_status = f"error: {e}"

    try:
        for name in ["policies", "historical_cases", "_semantic_cache"]:
            info = qdrant.get_collection(name)
            collections[name] = info.points_count
    except Exception as e:
        qdrant_status = f"error: {e}"

    overall = "healthy" if sqlite_status == "ok" and qdrant_status == "ok" else "degraded"

    return {
        "status": overall,
        "sqlite": sqlite_status,
        "qdrant": qdrant_status,
        "collections": collections,
    }
