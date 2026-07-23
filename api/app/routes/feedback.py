"""
Feedback route — Axis 6: Auto-mejora.

Thin HTTP handler — all orchestration logic lives in FeedbackService.
"""

import logging

from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)

from ..dependencies import get_feedback_service
from ..domain.models import FeedbackRequest, FeedbackResponse
from ..services.feedback import FeedbackService

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("/", status_code=200)
def submit_feedback(
    req: FeedbackRequest,
    service: FeedbackService = Depends(get_feedback_service),
) -> FeedbackResponse:
    """Submit analyst feedback. Auto-indexes high-quality cases as new precedents."""
    return service.submit(
        transaction_id=req.transaction_id,
        analyst_decision=req.analyst_decision,
        analyst_notes=req.analyst_notes,
        final_outcome=req.final_outcome,
        judge_score=req.judge_score,
        resolution=req.resolution,
    )
