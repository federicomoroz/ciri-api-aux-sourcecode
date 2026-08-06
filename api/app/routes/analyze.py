"""
Core analysis routes: /resolve and /judge.

Thin HTTP handlers — all orchestration logic lives in ResolutionService.
"""

import logging

from fastapi import APIRouter, Depends

from ..data.db import Database
from ..dependencies import get_db, get_resolution_service
from ..domain.constants import (
    ALERT_EVENT_BLOCKER_REJECT,
    ALERT_EVENT_HITL_REQUIRED,
    ALERT_SOURCE_RESOLVE,
)
from ..domain.enums import RiskLevel, Severity
from ..domain.models import JudgeRequest, JudgeResponse, ResolveRequest, ResolveResponse
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


def _emit_resolve_alerts(result: dict, tx_id: str, db: Database) -> None:
    """Emit operational alerts for significant resolve outcomes (best-effort)."""
    try:
        risk = result.get("risk_level", "")
        if risk == RiskLevel.BLOCKER:
            db.save_alert({
                "event_type": ALERT_EVENT_BLOCKER_REJECT,
                "severity": Severity.ERROR,
                "message": f"BLOCKER auto-reject: {tx_id}",
                "source": ALERT_SOURCE_RESOLVE,
                "transaction_id": tx_id,
                "metadata": {"risk_level": risk},
            })
        elif result.get("requires_hitl"):
            db.save_alert({
                "event_type": ALERT_EVENT_HITL_REQUIRED,
                "severity": Severity.WARN,
                "message": f"HITL requerido: {tx_id}",
                "source": ALERT_SOURCE_RESOLVE,
                "transaction_id": tx_id,
                "metadata": {"risk_level": risk},
            })
    except Exception:
        logger.warning("Failed to save alert for %s", tx_id, exc_info=True)


@router.post("/resolve", status_code=200)
def resolve(
    req: ResolveRequest,
    service: ResolutionService = Depends(get_resolution_service),
    db: Database = Depends(get_db),
) -> ResolveResponse:
    """Full resolution pipeline: policy eval -> log summary -> resolution synthesis -> guardrails."""
    result = service.resolve(req.to_context())

    tx_id = req.transaction_id or (req.tx_data.get("id", "") if req.tx_data else "")
    _emit_resolve_alerts(result, tx_id, db)

    return result


@router.post("/judge", status_code=200)
def judge(
    req: JudgeRequest,
    service: ResolutionService = Depends(get_resolution_service),
) -> JudgeResponse:
    """LLM-as-Judge: evaluate resolution quality across 5 criteria."""
    return service.judge(
        resolution=req.resolution,
        full_context=req.full_context,
    )
