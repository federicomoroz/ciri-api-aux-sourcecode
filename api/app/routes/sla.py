from fastapi import APIRouter, Depends

from ..analysis.analyzer import Analyzer
from ..data.db import Database
from ..dependencies import get_analyzer, get_db
from ..domain.models import SLACheckRequest

router = APIRouter(prefix="/api/sla", tags=["sla"])


@router.post("/check", status_code=200)
def check_sla(
    req: SLACheckRequest,
    analyzer: Analyzer = Depends(get_analyzer),
    db: Database = Depends(get_db),
) -> dict:
    """Check SLA compliance for a case.

    Si viene `transaction_id`, las fechas salen del caso historico: la apertura
    del reclamo y, cuando esta cerrado, su cierre. Resolverlo aca y no en el
    orquestador es lo que evita que n8n tenga que saber que existe una tabla de
    casos — y lo que hace que el plazo se mida contra el cierre real en vez de
    contra la fecha de hoy.
    """
    apertura, cierre = req.case_open_date, None
    if req.transaction_id:
        caso = db.get_case_for_transaction(req.transaction_id)
        if caso:
            apertura = caso.get("open_date") or apertura
            cierre = caso.get("close_date") or None

    return analyzer.check_sla(
        case_open_date=apertura,
        country=req.country,
        cliente_vip=req.cliente_vip,
        case_close_date=cierre,
    )
