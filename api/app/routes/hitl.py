"""Decision humana (HITL) — normaliza lo que contesto el analista.

Thin HTTP handler: las reglas viven en `domain/hitl.py`.

Existe porque esto era JavaScript adentro del workflow de n8n. El principio del
proyecto es que n8n orquesta y los nodos no contienen logica de negocio, y decidir
que se registra como aprobacion de una persona —y que resolucion entra al corpus
de precedentes— es logica de negocio de la parte mas cara de equivocarse.
"""

import logging

from fastapi import APIRouter

from ..domain import hitl
from ..domain.models import HitlDecideRequest, HitlDecideResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hitl", tags=["hitl"])


@router.post("/decide", status_code=200)
def decide(req: HitlDecideRequest) -> HitlDecideResponse:
    """Normaliza la decision del analista y arma el feedback que le corresponde."""
    decision = hitl.normalizar_decision(req.decision, req.notes)
    feedback = hitl.feedback_de(
        decision,
        transaction_id=req.transaction_id,
        judge_score=req.judge_score,
        resolution=req.resolution,
        motivo=req.motivo,
    )
    logger.info(
        "HITL %s: decision=%s outcome=%s indexa_precedente=%s",
        req.transaction_id,
        decision["analyst_decision"],
        decision["final_outcome"],
        feedback["resolution"] is not None,
    )
    return HitlDecideResponse(hitl_decision=decision, feedback=feedback)
