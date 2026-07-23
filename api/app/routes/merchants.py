import logging

from fastapi import APIRouter, Depends

from ..data.db import Database

logger = logging.getLogger(__name__)
from ..dependencies import get_db

router = APIRouter(prefix="/api/merchants", tags=["merchants"])


@router.get("/{name}/risk")
def get_merchant_risk(name: str, db: Database = Depends(get_db)) -> dict:
    """Raw merchant stats (CB ratio, volume, transaction history).
    Flags and risk classification computed in n8n via native Set nodes."""
    return db.get_merchant_stats(name)
