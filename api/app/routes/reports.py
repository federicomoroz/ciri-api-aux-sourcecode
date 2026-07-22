import logging

from fastapi import APIRouter, Depends

from ..config import Settings
from ..dependencies import get_report_generator, get_retriever, get_settings
from ..domain.models import ReportRequest
from ..rag.retriever import QdrantRetriever
from ..reports.generator import ReportGenerator
from .cache import _cache_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/html")
def generate_html_report(
    req: ReportRequest,
    generator: ReportGenerator = Depends(get_report_generator),
    retriever: QdrantRetriever = Depends(get_retriever),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Generate an HTML report and auto-store in cache for idempotency."""
    html = generator.render(req.model_dump())

    # Auto-cache: store HTML keyed by (transaction_id, motivo, cliente_vip)
    tx_id = req.transaction.get("id", "")
    if settings.semantic_cache_enabled and tx_id:
        try:
            key = _cache_key(tx_id, req.motivo, req.cliente_vip)
            retriever.store_in_cache(key, {"html": html})
            logger.info("Report cached for %s", tx_id)
        except Exception as e:
            logger.warning("Failed to cache report: %s", e)

    return {"html": html}
