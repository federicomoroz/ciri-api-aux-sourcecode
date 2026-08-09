from fastapi import APIRouter, Depends

from ..analysis.sla import CalculadoraDeSLA
from ..data.db import Database
from ..dependencies import get_db, get_sla
from ..domain.models import SLACheckRequest, SLACheckResponse

router = APIRouter(prefix="/api/sla", tags=["sla"])


@router.post("/check", status_code=200)
def check_sla(
    req: SLACheckRequest,
    sla: CalculadoraDeSLA = Depends(get_sla),
    db: Database = Depends(get_db),
) -> SLACheckResponse:
    """Check SLA compliance for a case.

    Si viene `transaction_id`, las fechas salen del caso historico **y solo de
    ahi**: la apertura del reclamo y, cuando esta cerrado, su cierre. Si no hay
    reclamo registrado, el plazo no se mide — no se sustituye por la fecha de la
    compra, que es otra cosa. Resolverlo aca y no en el
    orquestador es lo que evita que n8n tenga que saber que existe una tabla de
    casos — y lo que hace que el plazo se mida contra el cierre real en vez de
    contra la fecha de hoy.
    """
    # Con `transaction_id`, la apertura sale del caso historico y **el dato del
    # llamador no se mira**. El `or apertura` de antes era el agujero: si la
    # transaccion no tenia reclamo registrado —53 de las 100 del dataset— se caia
    # a lo que mandara el cliente, y el nodo de n8n manda la fecha de la compra.
    # Resolverlo en el contrato y no solo en un llamador es lo que hace que los
    # dos caminos —el panel y el orquestador— cuenten los mismos dias.
    if req.transaction_id:
        caso = db.get_case_for_transaction(req.transaction_id) or {}
        apertura = caso.get("open_date") or ""
        cierre = caso.get("close_date") or None
    else:
        apertura, cierre = req.case_open_date, None

    return sla.check_sla(
        case_open_date=apertura,
        country=req.country,
        cliente_vip=req.cliente_vip,
        case_close_date=cierre,
    )
