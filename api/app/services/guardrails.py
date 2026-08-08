"""Los seis guardrails, como una lista de reglas.

Un guardrail es una funcion que mira una resolucion y devuelve un aviso, o `None`
si no tiene nada que decir. Agregar el septimo es escribir una funcion y sumarla
a una tupla — antes era editar el cuerpo de `_validate_resolution` o el de
`_detect_divergence`, que devolvian una lista construida a mano.

El proyecto presenta «seis guardrails» como uno de sus diferenciadores, y ese
numero tiene que poder crecer sin tocar la logica que los recorre.

**Los dos momentos son sustantivos, no organizacion.** Las reglas de divergencia
solo pueden correr ANTES del override determinista: comparan lo que propuso el
modelo contra lo que decidio el codigo, y despues del override la propuesta ya no
existe — la resolucion entregada tiene la decision del codigo, y compararla
consigo misma no dice nada. Las otras dos corren DESPUES, sobre los campos que el
override no toca y que salen del modelo tal cual.

Ninguna corrige nada. De corregir se encarga el override; estas dejan constancia,
que es lo que un auditor necesita para saber que el modelo se equivoco.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ..domain.constants import (
    GUARDRAIL_MAX_COMPENSATION_RATIO,
    GUARDRAIL_MAX_CONFIDENCE,
    GUARDRAIL_MIN_FAILS_FOR_WARNING,
)
from ..domain.enums import ResolutionOutcome, RiskLevel, VerdictType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Revision:
    """Todo lo que una regla puede necesitar mirar, en un solo objeto.

    Un unico parametro y no cinco sueltos: asi agregar una regla que necesite un
    dato nuevo no obliga a cambiar la firma de las otras cinco ni la del
    recorredor. Es el mismo patron que `domain/context.py::CaseContext`.
    """

    resolucion: dict                      # la resolucion en su estado actual
    transaction: dict
    verdicts: list[dict] = field(default_factory=list)
    outcome: dict = field(default_factory=dict)

    @property
    def hay_blocker(self) -> bool:
        return any(v.get("verdict") == VerdictType.BLOCKER for v in self.verdicts)


Regla = Callable[[Revision], str | None]


# ── Antes del override: el modelo contra su propia evidencia ─────────────────

def approve_con_blocker(r: Revision) -> str | None:
    if r.resolucion.get("recommended_action") == ResolutionOutcome.APPROVE and r.hay_blocker:
        return (
            f"GUARDRAIL: el modelo propuso APPROVE con un veredicto BLOCKER activo — "
            f"corregido a {r.outcome['recommended_action']} (posible alucinacion)"
        )
    return None


def riesgo_blocker_inventado(r: Revision) -> str | None:
    if r.resolucion.get("risk_level") == RiskLevel.BLOCKER and not r.hay_blocker:
        return (
            f"GUARDRAIL: el modelo propuso risk_level=BLOCKER sin veredictos BLOCKER reales — "
            f"corregido a {r.outcome['risk_level']}"
        )
    return None


def reject_sin_blocker(r: Revision) -> str | None:
    if r.resolucion.get("recommended_action") == ResolutionOutcome.REJECT and not r.hay_blocker:
        return (
            f"GUARDRAIL: el modelo propuso REJECT sin veredictos BLOCKER — "
            f"corregido a {r.outcome['recommended_action']} (requiere revision humana)"
        )
    return None


def compensacion_contra_el_sla(r: Revision) -> str | None:
    """El modelo decide compensar contra lo que dice el plazo ya medido."""
    if "compensation_applicable" not in r.outcome:
        return None
    propuesta = bool(r.resolucion.get("compensation_applicable", False))
    if propuesta != r.outcome["compensation_applicable"]:
        return (
            f"GUARDRAIL: el modelo propuso compensation_applicable={propuesta} "
            f"contra un SLA que dice {r.outcome['compensation_applicable']} — "
            f"corregido segun POL-SLA-004"
        )
    return None


# ── Despues del override: los campos que el codigo no fija ──────────────────

def compensacion_excede_el_cargo(r: Revision) -> str | None:
    comp = r.resolucion.get("compensation_amount_usd", 0)
    monto = r.transaction.get("amount_usd", 0)
    if comp > monto * GUARDRAIL_MAX_COMPENSATION_RATIO and monto > 0:
        return (
            f"GUARDRAIL: Compensacion USD {comp:.2f} excede el monto original USD "
            f"{monto:.2f} en >{(GUARDRAIL_MAX_COMPENSATION_RATIO - 1) * 100:.0f}%"
        )
    return None


def confianza_excesiva(r: Revision) -> str | None:
    """Mucha seguridad sobre un caso con varias politicas violadas."""
    fallos = sum(
        1 for v in r.resolucion.get("policy_verdicts", [])
        if v.get("verdict") in (VerdictType.FAIL, VerdictType.BLOCKER)
    )
    confianza = r.resolucion.get("confidence", 0)
    if confianza > GUARDRAIL_MAX_CONFIDENCE and fallos >= GUARDRAIL_MIN_FAILS_FOR_WARNING:
        return (
            f"GUARDRAIL: Confianza excesiva ({confianza}) con {fallos} violaciones de politica"
        )
    return None


# El orden importa: es el que tienen los avisos en el informe.
ANTES_DEL_OVERRIDE: tuple[Regla, ...] = (
    approve_con_blocker,
    riesgo_blocker_inventado,
    reject_sin_blocker,
    compensacion_contra_el_sla,
)

DESPUES_DEL_OVERRIDE: tuple[Regla, ...] = (
    compensacion_excede_el_cargo,
    confianza_excesiva,
)


def evaluar(reglas: tuple[Regla, ...], revision: Revision, *, anotar: bool = False) -> list[str]:
    """Los avisos de esas reglas, en orden.

    `anotar` deja los avisos en el log ademas de devolverlos. Solo lo pide el
    momento previo al override: ahi el aviso es la unica constancia de que el
    modelo se contradijo, porque el override ya corrigio el dato.
    """
    avisos = [aviso for regla in reglas if (aviso := regla(revision))]
    if anotar:
        for aviso in avisos:
            logger.warning("%s", aviso)
    return avisos


def antes_del_override(resolucion: dict, outcome: dict, verdicts: list[dict]) -> list[str]:
    """Lo que el modelo propuso, contra lo que decidio el codigo.

    Se llama con la resolucion **sin corregir**: es la unica ventana en la que la
    propuesta del modelo todavia existe. Los avisos van al log ademas de
    devolverse, porque despues del override son la unica constancia de que el
    modelo se contradijo.
    """
    return evaluar(
        ANTES_DEL_OVERRIDE,
        Revision(resolucion=resolucion, transaction={}, outcome=outcome, verdicts=verdicts),
        anotar=True,
    )


def despues_del_override(resolucion: dict, transaction: dict) -> list[str]:
    """Los campos que el codigo no fija, y que salen del modelo tal cual."""
    return evaluar(
        DESPUES_DEL_OVERRIDE,
        Revision(resolucion=resolucion, transaction=transaction),
    )


CAMPOS_QUE_EL_OVERRIDE_PISA = (
    "recommended_action", "risk_level", "requires_hitl",
    "compensation_applicable", "confidence",
)


def propuesta_del_modelo(resolution: dict) -> dict:
    """Los campos que el override esta por sobrescribir, tal como los propuso.

    Se toma antes de la correccion. Despues no existe: la resolucion entregada
    tiene la decision del codigo, y calificar eso es calificar al codigo.
    """
    return {campo: resolution.get(campo) for campo in CAMPOS_QUE_EL_OVERRIDE_PISA}
