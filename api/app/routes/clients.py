from fastapi import APIRouter, Depends, HTTPException

from ..analysis.analyzer import Analyzer
from ..dependencies import get_analyzer

router = APIRouter(prefix="/api/clients", tags=["clients"])


@router.get("/{client_id}/history")
def get_client_history(client_id: str, analyzer: Analyzer = Depends(get_analyzer)) -> dict:
    """Client transaction history and risk flags.
    Used by n8n AI Agent as 'get_client_history' tool."""
    history = analyzer.client_flags(client_id)
    if history["total_transactions"] == 0:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
    return history
