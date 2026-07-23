import logging

from fastapi import APIRouter, Depends

from ..analysis.analyzer import Analyzer

logger = logging.getLogger(__name__)
from ..dependencies import get_analyzer
from ..domain.models import SLACheckRequest

router = APIRouter(prefix="/api/sla", tags=["sla"])


@router.post("/check", status_code=200)
def check_sla(req: SLACheckRequest, analyzer: Analyzer = Depends(get_analyzer)) -> dict:
    """Check SLA compliance for a case.
    Used by n8n AI Agent as 'check_sla' tool."""
    return analyzer.check_sla(
        case_open_date=req.case_open_date,
        country=req.country,
        cliente_vip=req.cliente_vip,
    )
