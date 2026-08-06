"""Analytics route — aggregated metrics dashboard."""

from fastapi import APIRouter, Depends

from ..data.db import Database
from ..dependencies import get_db
from ..domain.models import DashboardResponse

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/dashboard")
def dashboard(db: Database = Depends(get_db)) -> DashboardResponse:
    """Aggregated metrics across all processed cases."""
    return db.get_dashboard_stats()
