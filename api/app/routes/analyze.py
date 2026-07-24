"""
Core analysis routes: /resolve and /judge.

Thin HTTP handlers — all orchestration logic lives in ResolutionService.
"""

import logging

from fastapi import APIRouter, Depends

from ..data.db import Database
from ..dependencies import get_db, get_resolution_service
from ..domain.models import JudgeRequest, JudgeResponse, ResolveRequest, ResolveResponse
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.post("/resolve", status_code=200)
def resolve(
    req: ResolveRequest,
    service: ResolutionService = Depends(get_resolution_service),
    db: Database = Depends(get_db),
) -> ResolveResponse:
    """Full resolution pipeline: policy eval → log summary → resolution synthesis → guardrails."""
    result = service.resolve(
        tx_data=req.tx_data,
        policies=req.policies,
        similar_cases=req.similar_cases,
        logs=req.logs,
        merchant_risk=req.merchant_risk,
        client_history=req.client_history,
        motivo=req.motivo,
        cliente_vip=req.cliente_vip,
    )

    # Emit alerts for significant events (best-effort, never breaks pipeline)
    tx_id = req.tx_data.get("id", "") if req.tx_data else ""
    try:
        risk = result.get("risk_level", "")
        if risk == "BLOCKER":
            db.save_alert({
                "event_type": "blocker_auto_reject",
                "severity": "ERROR",
                "message": f"BLOCKER auto-reject: {tx_id}",
                "source": "resolve",
                "transaction_id": tx_id,
                "metadata": {"risk_level": risk},
            })
        elif result.get("requires_hitl"):
            db.save_alert({
                "event_type": "hitl_required",
                "severity": "WARNING",
                "message": f"HITL requerido: {tx_id}",
                "source": "resolve",
                "transaction_id": tx_id,
                "metadata": {"risk_level": risk},
            })
    except Exception:
        logger.warning("Failed to save alert for %s", tx_id, exc_info=True)

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
