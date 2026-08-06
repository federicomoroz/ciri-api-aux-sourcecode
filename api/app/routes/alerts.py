"""
Alerts route — operational event log.

POST /api/alerts    — store a runtime alert (from n8n error handler or pipeline)
GET  /api/alerts    — return recent alerts for the panel log section
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from ..data.db import Database
from ..dependencies import get_db
from ..domain.constants import ALERTS_DEFAULT_LIMIT
from ..domain.models import AlertRequest, AlertResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("/", status_code=201)
def create_alert(
    req: AlertRequest,
    db: Database = Depends(get_db),
) -> AlertResponse:
    """Store a runtime alert. Called by n8n error handler and resolve pipeline."""
    alert_id = db.save_alert(req.model_dump())
    logger.info(
        "Alert stored: id=%d type=%s severity=%s tx=%s",
        alert_id,
        req.event_type,
        req.severity,
        req.transaction_id,
    )
    return AlertResponse(
        id=alert_id,
        event_type=req.event_type,
        severity=req.severity,
        message=req.message,
        source=req.source,
        transaction_id=req.transaction_id,
        created_at=datetime.now(UTC).isoformat(),
    )


@router.get("/", status_code=200)
def list_alerts(
    limit: int = Query(ALERTS_DEFAULT_LIMIT, ge=1, le=200),
    db: Database = Depends(get_db),
) -> list[AlertResponse]:
    """Return the most recent alerts, newest first."""
    rows = db.get_recent_alerts(limit=limit)
    return [AlertResponse(**r) for r in rows]
