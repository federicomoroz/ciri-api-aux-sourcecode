import logging

from fastapi import APIRouter, Depends, HTTPException

from ..data.db import Database

logger = logging.getLogger(__name__)
from ..dependencies import get_db

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.get("")
def list_transactions(db: Database = Depends(get_db)) -> dict:
    """List all transactions (compact) — used by the test panel dropdown."""
    return {"transactions": db.list_transactions_compact()}


@router.get("/{txn_id}")
def get_transaction(txn_id: str, db: Database = Depends(get_db)) -> dict:
    """Exact lookup by TXN-XXXXX. Used by n8n AI Agent as 'lookup_transaction' tool."""
    tx = db.get_transaction(txn_id)
    if not tx:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")
    return tx
