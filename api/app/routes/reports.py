import logging

from fastapi import APIRouter, Depends

from ..config import Settings
from ..data.db import Database, cache_key
from ..data.precomputados import informe_demo
from ..dependencies import get_db, get_report_generator, get_settings
from ..domain.models import ReportRequest
from ..reports.generator import ReportGenerator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/html", status_code=200)
def generate_html_report(
    req: ReportRequest,
    generator: ReportGenerator = Depends(get_report_generator),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Generate an HTML report and auto-store in SQLite cache for idempotency."""
    # Si la resolucion es un ejemplo de otro caso, el informe tiene que ser ese
    # ejemplo entero. Renderizar la transaccion pedida con la resolucion de otra
    # produciria un informe que se lee como verdadero y no lo es.
    ejemplo_de = (req.resolution or {}).get("demo_ejemplo_de")
    if ejemplo_de:
        prestado = (req.resolution or {}).get("transaction_id", "")
        html = informe_demo(settings.demo_reports_path, prestado, solicitada=ejemplo_de)
        if html is not None:
            logger.warning(
                "MODO DEMO: el informe pedido para %s se responde con el de %s, "
                "que es de donde vino la resolucion.", ejemplo_de, prestado,
            )
            return {"html": html}

    html = generator.render(req.model_dump())

    # Auto-cache: store HTML keyed by (transaction_id, cliente_vip)
    tx_id = req.transaction.get("id", "")
    if settings.report_cache_enabled and tx_id:
        try:
            key = cache_key(tx_id, req.cliente_vip)
            db.store_cached_report(key, html)
            logger.info("Report cached for %s", tx_id)
        except Exception as e:
            logger.warning("Failed to cache report: %s", e)

    return {"html": html}
