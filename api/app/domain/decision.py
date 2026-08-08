"""Que decide el codigo, sin preguntarle al modelo.

Aca vive la tesis del proyecto: **el codigo decide y el LLM explica**. La accion
recomendada, el nivel de riesgo, si hace falta un analista y cuanta compensacion
corresponde salen de los veredictos de politica y del resultado del SLA — no de
lo que el modelo haya propuesto. `ResolutionService` toma esa decision y despues
sobrescribe con ella lo que dijo el modelo.

Son funciones puras sobre `dict`: no conocen el cliente del modelo, ni el tracer,
ni la base. Vivian dentro de `ResolutionService` como quince metodos estaticos, y
esa lógica arrastraba `Tracer`, `LLMClient` y `ModelosService` en su grafo de
dependencias por el solo hecho de compartir clase. Aca se prueban sin un mock.

Las dos reglas que mas importan, y por que son de esta capa y no del modelo:

- **Falla cerrado.** Sin veredictos, o con veredictos ilegibles, se deriva a una
  persona. «Ninguna politica fallo» y «no se pudo evaluar ninguna politica» no
  son lo mismo, y cuando daban lo mismo, una caida de Qdrant aprobaba
  contracargos sola.
- **Un BLOCKER lo emite solo una politica habilitada para bloquear**, y eso sale
  de la politica misma, no de una lista aca.
"""

import logging

from .constants import (
    FRAUD_SCORE_DEFAULT,
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    POLICY_SEED_BLOQUEANTES,
    RISK_FRAUD_SEVERE,
    RISK_HIGH_MIN_FAILS,
    SLA_COMPENSATION_MAX_USD,
)
from .enums import ResolutionOutcome, RiskLevel, VerdictType
from .models import ResolutionOutput

logger = logging.getLogger(__name__)

# Los veredictos que el sistema sabe leer, derivados del enum. Enumerarlos a mano
# dejaba el mensaje de error mintiendo en cuanto alguien agregaba uno.
VERDICTOS_VALIDOS: frozenset[str] = frozenset(v.value for v in VerdictType)
# Con "/" y no ", ": el separador viaja al informe dentro del motivo de la
# derivacion a un analista, y cambiarlo cambia lo que lee quien resuelve el caso.
VEREDICTOS_LEGIBLES: str = "/".join(sorted(VERDICTOS_VALIDOS))


def codigos_con_veredicto(policy_verdicts: list[dict], *veredictos: str) -> list[str]:
    """Codigos de las politicas con alguno de esos veredictos."""
    return [
        v.get("policy_code", "?") for v in policy_verdicts
        if v.get("verdict") in veredictos
    ]


def politicas_que_pueden_bloquear(policies: list[dict]) -> frozenset[str]:
    """Que politicas de las recuperadas tienen permitido bloquear.

    Sale de la politica misma (`puede_bloquear`, columna de SQLite que viaja
    en la carga de Qdrant), no de una lista en el codigo: agregar una
    politica bloqueante es un POST, no un deploy.

    Si el documento indexado no trae el campo —un indice armado antes de que
    la columna existiera—, se cae a la semilla del dataset. Reindexar la
    politica, o editarla por la API, la trae al dia.
    """
    return frozenset(
        p.get("code") for p in policies if habilitada_para_bloquear(p)
    ) - {None}


def habilitada_para_bloquear(policy: dict) -> bool:
    """Si esta politica, sola, tiene permitido emitir un BLOCKER.

    El mismo criterio que usa el conjunto de arriba, con una politica por vez:
    lo necesita el formateador del prompt, para poder decirle al modelo cuales
    pueden bloquear en vez de nombrarlas por codigo. Que sea una sola funcion
    es lo que evita que el prompt y la degradacion posterior discrepen — si
    discreparan, el modelo emitiria un BLOCKER que el codigo despues degrada,
    y el veredicto quedaria distinto de lo que el prompt pidio.
    """
    if "puede_bloquear" in policy:
        return policy["puede_bloquear"] in (1, True, "1", "true")
    return policy.get("code") in POLICY_SEED_BLOQUEANTES


def degradar_blockers_no_habilitados(verdicts: list[dict], policies: list[dict]) -> list[dict]:
    """Downgrade invalid BLOCKER verdicts to FAIL.

    Solo las politicas marcadas como bloqueantes pueden emitir un BLOCKER
    legitimo. El resto es sobre-escalada del modelo (por ejemplo, tratar la
    suspension de un comercio como si fuera irreversible).
    """
    habilitadas = politicas_que_pueden_bloquear(policies)
    for v in verdicts:
        if (
            v.get("verdict") == VerdictType.BLOCKER
            and v.get("policy_code") not in habilitadas
        ):
            logger.warning(
                "BLOCKER degradado a FAIL en %s: la politica no esta marcada como bloqueante",
                v.get("policy_code"),
            )
            v["verdict"] = VerdictType.FAIL
            v["requires_human_review"] = True
    return verdicts


def nivel_de_riesgo(
    policy_verdicts: list[dict],
    has_blocker: bool,
    fail_count: int,
    fraud_score: int,
) -> tuple[str, str]:
    """(nivel, motivo). El motivo se escribe aca para que viaje con el nivel."""
    codigos_fallidos = codigos_con_veredicto(
        policy_verdicts, VerdictType.FAIL, VerdictType.BLOCKER,
    )

    if has_blocker:
        bloqueantes = codigos_con_veredicto(policy_verdicts, VerdictType.BLOCKER)
        return RiskLevel.BLOCKER, (
            f"Veredicto BLOCKER en {', '.join(bloqueantes)} (transaccion irreversible)"
        )

    if fail_count >= RISK_HIGH_MIN_FAILS or fraud_score < RISK_FRAUD_SEVERE:
        motivos = []
        if fail_count >= RISK_HIGH_MIN_FAILS:
            motivos.append(f"{fail_count} violaciones de politica ({', '.join(codigos_fallidos)})")
        if fraud_score < RISK_FRAUD_SEVERE:
            motivos.append(f"fraud_score={fraud_score} (umbral severo: {RISK_FRAUD_SEVERE})")
        if fraud_score >= FRAUD_SCORE_HIGH_RISK_THRESHOLD:
            # El riesgo viene de la politica, no del fraude: conviene decirlo.
            motivos.append(
                f"fraud_score={fraud_score} indica bajo riesgo de fraude — "
                f"riesgo HIGH es por violaciones de politica, no por fraude"
            )
        return RiskLevel.HIGH, f"HIGH por: {', '.join(motivos)}"

    if fail_count >= 1 or fraud_score < FRAUD_SCORE_HIGH_RISK_THRESHOLD:
        nota_fraude = (
            f" (fraud_score={fraud_score} seguro, riesgo es de politica)"
            if fraud_score >= FRAUD_SCORE_HIGH_RISK_THRESHOLD else ""
        )
        nota_codigos = f" ({', '.join(codigos_fallidos)})" if codigos_fallidos else ""
        return RiskLevel.MEDIUM, (
            f"MEDIUM por: {fail_count} violacion(es){nota_codigos}, "
            f"fraud_score={fraud_score}{nota_fraude}"
        )

    return RiskLevel.LOW, f"LOW: sin violaciones, fraud_score={fraud_score} (seguro)"


def accion_recomendada(has_blocker: bool, fail_count: int, needs_human: bool) -> tuple[str, bool, str | None]:
    """(accion, requiere persona, motivo). Un BLOCKER se resuelve solo; una
    violacion sin BLOCKER siempre pasa por un analista."""
    if has_blocker:
        return ResolutionOutcome.REJECT, False, None
    if fail_count > 0:
        return ResolutionOutcome.PENDING_HITL, True, (
            f"{fail_count} violacion(es) de politica — requiere revision de analista"
        )
    if needs_human:
        return ResolutionOutcome.PENDING_HITL, True, (
            "Evaluacion de politicas requiere revision humana"
        )
    return ResolutionOutcome.APPROVE, False, None


def compensacion_por_sla(sla: dict, tx_data: dict) -> dict:
    """Compensacion a partir del SLA ya calculado. Sin LLM.

    POL-SLA-004: incumplir el plazo habilita compensacion, con tope fijo. Los
    dias habiles ya los conto `Analyzer.check_sla`; aca solo se lee su
    resultado, porque un umbral contra una fecha es exactamente lo que el
    codigo resuelve mejor que un modelo.

    Sin dato de SLA no hay nada que determinar: se devuelve vacio y la
    decision queda en el modelo, sujeta a `_validate_resolution`.

    `within_sla` puede venir en None: es un reclamo sin fecha de apertura
    registrada, o sea un plazo que no se pudo medir. Eso **no** es un
    incumplimiento. Con `not sla.get("within_sla", True)` un None daba True
    y la compensacion salia igual, que es el mismo defecto por otro camino.
    """
    if not sla or "within_sla" not in sla:
        return {}
    incumplido = sla.get("within_sla") is False
    monto = float(tx_data.get("amount_usd", 0) or 0)
    return {
        "compensation_applicable": incumplido,
        # Compensar mas que el cargo original no tiene sentido, y el tope de
        # la politica es un maximo, no un monto fijo.
        "compensation_amount_usd": (
            round(min(SLA_COMPENSATION_MAX_USD, monto), 2) if incumplido and monto else 0.0
        ),
    }


def decidir(policy_verdicts: list[dict], tx_data: dict) -> dict:
    """Accion y riesgo a partir de los veredictos. Sin LLM.

    Reglas:
    - Algun veredicto BLOCKER → REJECT + riesgo BLOCKER
    - Algun FAIL (sin BLOCKER) → PENDING_HITL + riesgo HIGH o MEDIUM
    - Algun requires_human_review → PENDING_HITL (red de seguridad)
    - Todo PASS/WARNING → APPROVE + riesgo LOW o MEDIUM
    - Sin veredictos → PENDING_HITL: no hay evidencia de nada
    - Con veredictos ilegibles → PENDING_HITL: la evidencia no se pudo leer
    """
    if not policy_verdicts:
        # Falla cerrado. "Ninguna politica fallo" y "no se evaluo ninguna
        # politica" no son lo mismo, y sin esta rama daban lo mismo: APPROVE.
        # Se llega aca por dos caminos reales —Qdrant caido, que el nodo
        # `Buscar Politicas` deja pasar con `continueRegularOutput`, o un JSON
        # invalido del modelo, que `validate_llm_output` degrada a lista
        # vacia—, o sea que una falla de infraestructura terminaba aprobando
        # contracargos sola.
        logger.error("Sin veredictos de politica: se deriva a revision humana")
        return {
            "recommended_action": ResolutionOutcome.PENDING_HITL,
            "risk_level": RiskLevel.HIGH,
            "risk_reason": "No se pudo evaluar ninguna politica: sin evidencia no hay decision",
            "requires_hitl": True,
            "hitl_reason": (
                "No se evaluo ninguna politica — revisar si el vector store respondio "
                "y si la evaluacion del modelo devolvio un JSON valido"
            ),
        }

    # Un veredicto con el enum mal escrito valia lo mismo que uno que dice
    # PASS: `has_blocker` daba False, `fail_count` daba cero, y un caso
    # cripto con score 8 salia APPROVE sin revision humana. Con los enums
    # bien escritos el mismo caso da REJECT + BLOCKER.
    #
    # El parseo no los traduce a proposito —adivinar el enum mas parecido
    # seria decidir por el modelo, y `test_un_veredicto_que_no_existe_no_se_
    # traduce` lo fija—, asi que la decision de que hacer con ellos es de
    # aca: lo mismo que con la lista vacia. Un veredicto ilegible no es un
    # veredicto favorable; es evidencia que no se pudo leer.
    #
    # Importa mas desde que el modo demo corre con modelos que no son
    # Claude: los prompts piden el enum exacto, pero un modelo mas chico
    # escribe BLOCKED o FAILED, y ahi el sistema aprobaba solo.
    ilegibles = [
        v.get("policy_code", "?") for v in policy_verdicts
        if v.get("verdict") not in VERDICTOS_VALIDOS
    ]
    if ilegibles:
        logger.error(
            "Veredictos con un valor que no existe (%s): se deriva a revision humana",
            ", ".join(ilegibles),
        )
        return {
            "recommended_action": ResolutionOutcome.PENDING_HITL,
            "risk_level": RiskLevel.HIGH,
            "risk_reason": (
                f"{len(ilegibles)} veredicto(s) con un valor que no existe: la evidencia "
                f"no se pudo leer"
            ),
            "requires_hitl": True,
            "hitl_reason": (
                f"El modelo devolvio veredictos que no son {VEREDICTOS_LEGIBLES} en "
                f"{', '.join(ilegibles)} — revisar la salida del modelo antes de decidir"
            ),
        }

    has_blocker = any(v.get("verdict") == VerdictType.BLOCKER for v in policy_verdicts)
    fail_count = len(
        codigos_con_veredicto(policy_verdicts, VerdictType.FAIL, VerdictType.BLOCKER)
    )
    needs_human = any(v.get("requires_human_review") is True for v in policy_verdicts)
    fraud_score = int(tx_data.get("fraud_score", FRAUD_SCORE_DEFAULT))

    risk_level, risk_reason = nivel_de_riesgo(
        policy_verdicts, has_blocker, fail_count, fraud_score,
    )
    action, requires_hitl, hitl_reason = accion_recomendada(
        has_blocker, fail_count, needs_human,
    )

    return {
        "recommended_action": action,
        "risk_level": risk_level,
        "risk_reason": risk_reason,
        "requires_hitl": requires_hitl,
        "hitl_reason": hitl_reason,
    }


def con_piso(resolution: dict, outcome: dict) -> dict:
    """Garantiza que la resolucion tenga todos sus campos.

    `validate_llm_output` devuelve el dict crudo cuando la validacion falla,
    para que los guardrails puedan mirarlo. Pero si el modelo no devolvio
    nada utilizable —una respuesta truncada, por ejemplo— ese dict puede
    estar vacio, y entonces la plantilla del informe explota con un 500 al
    pedirle un campo que no existe.

    Un modelo que falla es un caso degradado, no una excepcion del proceso:
    la resolucion sale con la decision que tomo el codigo, los textos vacios
    y la constancia de que el modelo no aporto nada.
    """
    if resolution.get("recommended_action"):
        return resolution
    logger.error(
        "El modelo no devolvio una resolucion utilizable: se arma con la decision "
        "deterministica y se deja constancia",
    )
    piso = ResolutionOutput(
        recommended_action=outcome.get("recommended_action", ResolutionOutcome.PENDING_HITL),
        risk_level=outcome.get("risk_level", RiskLevel.HIGH),
        confidence=0.0,
        justification=(
            "El modelo no devolvio una resolucion utilizable para este caso. La accion y "
            "el nivel de riesgo son los que calculo el sistema a partir de los veredictos "
            "de politica; no hay analisis del modelo que mostrar."
        ),
    ).model_dump()
    return {**piso, **{k: v for k, v in resolution.items() if v not in (None, "", [])}}
