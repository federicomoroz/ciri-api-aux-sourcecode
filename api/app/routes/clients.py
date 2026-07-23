import logging

from fastapi import APIRouter, Depends, HTTPException

from ..data.db import Database

logger = logging.getLogger(__name__)
from ..dependencies import get_db

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.get("/{client_id}/history")
def get_client_history(client_id: str, db: Database = Depends(get_db)) -> dict:
    """Raw client transaction history and chargeback counts.
    Risk flags computed in n8n via native Set nodes."""
    history = db.get_client_history(client_id)
    if history["total_transactions"] == 0:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
    return history
