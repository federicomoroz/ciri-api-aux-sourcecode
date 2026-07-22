from fastapi import APIRouter, Depends

from ..analysis.analyzer import Analyzer
from ..dependencies import get_analyzer

router = APIRouter(prefix="/api/merchants", tags=["merchants"])


@router.get("/{name}/risk")
def get_merchant_risk(name: str, analyzer: Analyzer = Depends(get_analyzer)) -> dict:
    """Compute merchant risk profile (CB ratio, volume, flags).
    Used by n8n AI Agent as 'merchant_risk_profile' tool."""
    return analyzer.merchant_risk_profile(name)
