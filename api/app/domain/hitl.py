"""Que hacer con la decision de un analista. Sin LLM y sin n8n.

Esta logica vivia en el nodo `Procesar Respuesta HITL` del workflow: ochenta y
ocho lineas de JavaScript dentro de un JSON. Funcionaba, pero estaba fuera del
alcance de `pytest`, de `ruff` y de `constants.py`, y ya habia costado dos bugs
que el propio nodo documenta en sus comentarios — el `MODIFY` que se registraba
como aprobacion, y el vencimiento del plazo que caia en `APPROVE`.

El principio del proyecto es que n8n orquesta y **los nodos no contienen logica
de negocio**. Esto es logica de negocio: decide que se registra como decision de
una persona y que resolucion entra al corpus de precedentes con el que se
resuelven los casos siguientes.

Las tres reglas, y por que ninguna puede quedar en el orquestador:

- **Falla cerrado.** Sin decision no hay aprobacion. Si el plazo vence, el caso
  sale marcado como pendiente y no se registra ninguna decision de analista: un
  contracargo de riesgo alto no puede aprobarse porque nadie contesto.
- **`MODIFY` no es `APPROVE`.** El formulario ofrece tres opciones. Mientras el
  mapeo tuvo dos, quien elegia «modificar» quedaba registrado como si hubiera
  aprobado, y con nota del juez >= 8 el caso se indexaba como precedente
  aprobado — envenenando el corpus que alimenta las decisiones siguientes.
- **Solo se indexa lo que una persona avalo TAL CUAL.** Con `MODIFY` la aprobo
  con cambios que el sistema no tiene, asi que guardar esta version como
  precedente seria guardar algo que nadie aprobo.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .constants import (
    HITL_APROBADA,
    HITL_DECISION_SIN_RESPUESTA,
    HITL_NOTAS_POR_VENCIMIENTO,
    HITL_RESULTADO_POR_DECISION,
)
from .enums import ResolutionOutcome


def normalizar_decision(decision: str | None, notas: str | None) -> dict:
    """La decision del analista en la forma que guarda el sistema.

    `decision` vacio significa que el plazo vencio sin respuesta, que no es lo
    mismo que una respuesta que no se entiende: la primera sale marcada con
    `timeout`, la segunda como pendiente a secas. Las dos, sin aprobar.
    """
    cruda = (decision or "").strip()
    notas_limpias = (notas or "").strip()
    ahora = datetime.now(UTC).isoformat()

    if not cruda:
        return {
            "analyst_decision": HITL_DECISION_SIN_RESPUESTA,
            "analyst_notes": HITL_NOTAS_POR_VENCIMIENTO,
            "final_outcome": ResolutionOutcome.PENDING_HITL,
            "timestamp": ahora,
            "timeout": True,
        }

    normalizada = cruda.upper()
    return {
        "analyst_decision": normalizada,
        "analyst_notes": notas_limpias,
        # Sin mapeo conocido no se asume aprobacion: es lo mismo que hace el
        # resto del sistema con una entrada que no entiende.
        "final_outcome": HITL_RESULTADO_POR_DECISION.get(
            normalizada, ResolutionOutcome.PENDING_HITL,
        ),
        "timestamp": ahora,
        "timeout": False,
    }


def avalada_tal_cual(hitl: dict) -> bool:
    """Si esta resolucion es exactamente la que una persona aprobo."""
    return not hitl.get("timeout", False) and hitl["final_outcome"] == HITL_APROBADA


def feedback_de(
    hitl: dict,
    transaction_id: str,
    judge_score: float,
    resolution: dict | None,
    motivo: str | None = None,
) -> dict:
    """El cuerpo de `POST /api/feedback` para esta decision.

    `resolution` viaja solo si el analista avalo la resolucion tal cual. En
    cualquier otro caso va en `None` y el precedente no se indexa, que es lo
    unico que evita que el corpus se llene de casos que nadie reviso.
    """
    return {
        "transaction_id": transaction_id,
        "analyst_decision": hitl["analyst_decision"],
        "analyst_notes": hitl["analyst_notes"],
        "final_outcome": hitl["final_outcome"],
        "judge_score": judge_score,
        "motivo": motivo or None,
        "resolution": resolution if avalada_tal_cual(hitl) else None,
    }
