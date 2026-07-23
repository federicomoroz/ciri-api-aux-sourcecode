"""
Langfuse observability stats route.

GET /api/langfuse/stats — returns trace/token/cost/score stats for the test panel.
"""

import logging

from fastapi import APIRouter, Depends

logger = logging.getLogger(__name__)

from ..dependencies import get_langfuse_stats_service
from ..domain.models import LangfuseStatsResponse
from ..services.langfuse_stats import LangfuseStatsService

router = APIRouter(prefix="/api/langfuse", tags=["langfuse"])


@router.get("/stats", response_model=LangfuseStatsResponse)
def langfuse_stats(
    service: LangfuseStatsService = Depends(get_langfuse_stats_service),
) -> dict:
    """Return Langfuse observability stats for display in the test panel."""
    return service.get_stats()
