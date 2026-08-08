"""Como se le cuenta al modelo lo que ya paso, y lo que dicen los logs.

El resumen de precedentes **no lo escribe el modelo**: se arma aca, a partir de
los casos que devolvio el RAG. Es una decision deliberada — si el resumen de la
evidencia lo redactara el mismo modelo que despues la usa para decidir, no habria
forma de distinguir un precedente real de uno recordado de más.

Cada precedente sale etiquetado con `[MOTIVO SIMILAR]` cuando el motivo coincide
por sinonimos y `[MISMO MERCHANT]` cuando el comercio es el mismo, y el bloque
cierra con la tendencia contada sobre todos: cuantos se aprobaron y cuantos se
rechazaron. El modelo recibe hechos contados, no una narracion.

Lo mismo con los logs: `resumir_logs` cuenta severidades y nombra los patrones
que detecto `Analyzer.detect_error_patterns`, sin LLM. «Timeout sistematico del
comercio» dice bastante mas que seis lineas repetidas de MERCHANT_NO_RESPONSE.

Son funciones puras: reciben listas de `dict` y devuelven texto.
"""

import logging

from ..analysis.analyzer import Analyzer
from ..rag.formatter import annotate_by_motivo
from .constants import LLM_MAX_CRITICAL_LOGS
from .enums import Severity

logger = logging.getLogger(__name__)


# Como se lee una resolucion previa. Fuente unica: las mismas palabras deciden
# la implicacion que se le cuenta al modelo y el conteo de la tendencia. Estaban
# escritas en tres lugares del mismo metodo. Se evalua en orden.
CLASES_DE_RESOLUCION: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("sin_resolver", ("sin resolucion", "pendiente"),
     "caso similar permanece sin resolver — sugiere que este tipo de caso requiere "
     "investigacion adicional antes de decidir"),
    ("cerrado", ("cerrado",),
     "caso similar fue cerrado sin resolucion explicita — riesgo de que el caso actual "
     "siga el mismo camino si no se investiga la causa raiz antes de decidir"),
    ("aprobado", ("aprobado", "a favor"),
     "precedente fue aprobado — patron favorable al cliente para este tipo de caso"),
    ("rechazado", ("rechazado", "denegado"),
     "precedente fue rechazado — patron desfavorable al cliente para este tipo de caso"),
    ("parcial", ("parcial",),
     "precedente resuelto con reembolso parcial — solucion intermedia para este tipo de caso"),
)


def clasificar_resolucion(resolution: str) -> tuple[str | None, str | None]:
    """(clase, implicacion) de como se resolvio un precedente."""
    texto = (resolution or "").lower()
    for clase, claves, implicacion in CLASES_DE_RESOLUCION:
        if any(k in texto for k in claves):
            return clase, implicacion
    return None, None


def describir_precedente(
    case: dict,
    label: str | None,
    match_source: str | None,
    tx_merchant: str,
) -> str:
    """Una linea por precedente, con sus etiquetas y su implicacion."""
    case_merchant = case.get("merchant", "")
    motivo = case.get("motivo", "?")
    resolution = case.get("resolution", "?")

    tags = []
    if label:
        tags.append("[MOTIVO SIMILAR]")
    if tx_merchant and case_merchant and case_merchant.lower() == tx_merchant.lower():
        tags.append("[MISMO MERCHANT]")

    linea = (
        f"{case.get('case_id', '?')}{' ' + ' '.join(tags) if tags else ''}: "
        f"{motivo}, {resolution} en {case.get('resolution_days', '?')}d"
    )
    if case_merchant:
        linea += f", merchant={case_merchant}"
    if not label:
        return linea

    obs = case.get("observations", "")
    if obs:
        linea += f". Obs: {obs}"
    origen = (
        f" (match por {match_source}, motivo registrado: {motivo})"
        if match_source == "observaciones" else ""
    )
    linea += f". Relevancia: mismo patron de {label}{origen}"

    _, implicacion = clasificar_resolucion(resolution)
    return f"{linea}. Nota: {implicacion}" if implicacion else linea


def resumir_precedentes(
    similar_cases: list[dict],
    current_motivo: str | None,
    tx_merchant: str = "",
) -> str:
    """Build precedent_summary deterministically. No LLM involved.

    Extracts case_id, motivo, resolution, resolution_days from each case.
    Tags [MOTIVO SIMILAR] using synonym matching and sorts matches first.
    Tags [MISMO MERCHANT] when precedent merchant matches current transaction.
    """
    if not similar_cases:
        return "Sin precedentes relevantes."

    annotated = annotate_by_motivo(similar_cases, current_motivo)

    parts = [
        describir_precedente(c, label, match_source, tx_merchant)
        for c, label, match_source in annotated
    ]

    # Tendencia sobre TODOS los precedentes, con el mismo criterio que las notas.
    clases = [
        clasificar_resolucion(c.get("resolution", ""))[0]
        for c, _, _ in annotated
    ]
    aprobados = clases.count("aprobado")
    rechazados = clases.count("rechazado")
    total = len(annotated)

    if aprobados > rechazados:
        tendencia = "tendencia favorable al cliente"
    elif rechazados > aprobados:
        tendencia = "tendencia desfavorable al cliente"
    else:
        tendencia = "sin tendencia clara"

    patron = (
        f"Patron: de {total} precedentes, {aprobados} aprobados, "
        f"{rechazados} rechazados — {tendencia}"
    )

    con_motivo = [c for c, label, _ in annotated if label is not None]
    if con_motivo:
        aprobados_motivo = sum(
            1 for c in con_motivo
            if clasificar_resolucion(c.get("resolution", ""))[0] == "aprobado"
        )
        patron += (
            f". Motivo similar: {len(con_motivo)}/{total}, {aprobados_motivo} aprobados"
        )
    parts.append(patron)

    return " | ".join(parts)


def resumir_logs(logs: list[dict]) -> str:
    """Resume los logs para el prompt: conteos, patrones y eventos criticos.

    Los patrones los detecta `Analyzer.detect_error_patterns`, sin LLM. Es lo
    que convierte una lista de eventos sueltos en una senal aprovechable:
    "timeout sistematico del comercio" dice bastante mas que seis lineas
    repetidas de MERCHANT_NO_RESPONSE.
    """
    analysis = Analyzer.detect_error_patterns(logs)
    severity_counts = analysis["severity_counts"]
    text = (
        f"Total: {len(logs)} eventos | "
        f"ERROR: {severity_counts[Severity.ERROR]} | "
        f"WARN: {severity_counts[Severity.WARN]} | "
        f"INFO: {severity_counts[Severity.INFO]}\n"
    )
    if analysis["patterns"]:
        text += f"Patrones detectados: {', '.join(analysis['patterns'])}\n"
    for log in analysis["critical_events"][:LLM_MAX_CRITICAL_LOGS]:
        text += f"- [{log['severity']}] {log['event']}: {log['detail']}\n"
    return text
