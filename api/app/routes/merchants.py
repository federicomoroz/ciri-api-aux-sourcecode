from fastapi import APIRouter, Depends

from ..analysis.analyzer import Analyzer
from ..dependencies import get_analyzer
from ..domain.models import MerchantRiskResponse

router = APIRouter(prefix="/api/merchants", tags=["merchants"])


@router.get("/{name}/risk")
def get_merchant_risk(name: str, analyzer: Analyzer = Depends(get_analyzer)) -> MerchantRiskResponse:
    """Merchant risk profile: CB ratio, volume, flags, strategic status."""
    return analyzer.merchant_risk_profile(name)
