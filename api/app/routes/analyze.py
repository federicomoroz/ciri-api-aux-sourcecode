"""
Core analysis routes: /resolve and /judge.

Thin HTTP handlers — all orchestration logic lives in ResolutionService.
"""

from fastapi import APIRouter, Depends

from ..dependencies import get_resolution_service
from ..domain.models import JudgeRequest, JudgeResponse, ResolveRequest, ResolveResponse
from ..services.resolution import ResolutionService

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.post("/resolve", status_code=200)
def resolve(
    req: ResolveRequest,
    service: ResolutionService = Depends(get_resolution_service),
) -> ResolveResponse:
    """Full resolution pipeline: policy eval → log summary → resolution synthesis → guardrails."""
    return service.resolve(
        tx_data=req.tx_data,
        policies=req.policies,
        similar_cases=req.similar_cases,
        logs=req.logs,
        merchant_risk=req.merchant_risk,
        client_history=req.client_history,
        motivo=req.motivo,
        cliente_vip=req.cliente_vip,
    )


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
