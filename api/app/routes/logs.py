from fastapi import APIRouter, Depends

from ..analysis.analyzer import Analyzer
from ..data.db import Database
from ..dependencies import get_db

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/{tx_id}")
def get_logs(tx_id: str, db: Database = Depends(get_db)) -> dict:
    """All logs for a transaction, ordered by timestamp.
    Used by n8n AI Agent as 'get_logs' tool."""
    logs = db.get_logs_for_transaction(tx_id)
    return {
        "transaction_id": tx_id,
        "log_count": len(logs),
        "logs": logs,
        "severity_summary": Analyzer.count_severities(logs),
    }
