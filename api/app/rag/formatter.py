"""
Prompt formatters for RAG results.

Single source of truth for formatting policies and cases into LLM-readable text.
Extracted from QdrantRetriever to keep retrieval and presentation concerns separate.
"""

from ..domain.constants import DISPLAY_FALLBACK, SIMILAR_CASES_SCORE_THRESHOLD

# Synonym groups for mechanical motivo matching.
# Each group is a (label, keywords) tuple — label is used in the summary
# to explain WHY the case matched (e.g., "Relevancia: mismo patrón de cargo duplicado").
_MOTIVO_SYNONYM_GROUPS: list[tuple[str, set[str]]] = [
    ("cargo duplicado", {"duplicado", "duplicada", "doble", "doble cobro", "doble cargo", "cargo doble"}),
    ("fraude / no reconocido", {"no reconoce", "no reconocida", "no autorizado", "no autorizada", "fraude"}),
    ("producto no recibido", {"no recibido", "no entregado", "no entrega", "falta entrega", "no llego"}),
    ("producto defectuoso", {"defecto", "defectuoso", "calidad", "dañado", "roto"}),
    ("monto incorrecto", {"monto incorrecto", "monto erroneo", "cobro incorrecto"}),
    ("cancelacion", {"cancelado", "cancelacion", "post-cancelacion", "post cancelacion"}),
]


def motivo_match_label(motivo_a: str, motivo_b: str) -> str | None:
    """Return the synonym group label if motivos match, else None."""
    a = motivo_a.lower()
    b = motivo_b.lower()
    for label, keywords in _MOTIVO_SYNONYM_GROUPS:
        a_match = any(kw in a for kw in keywords)
        b_match = any(kw in b for kw in keywords)
        if a_match and b_match:
            return label
    return None


def envolver_resultados(resultados: list[dict], formateado: str, consulta_por_defecto: str = "") -> dict:
    """Sobre comun de las rutas de busqueda semantica.

    `_query` la agrega el retriever con la consulta enriquecida que realmente
    ejecuto: devolverla es lo que hace auditable la busqueda.
    """
    return {
        "query_used": resultados[0].get("_query", consulta_por_defecto) if resultados else consulta_por_defecto,
        "results": resultados,
        "formatted_for_llm": formateado,
        "count": len(resultados),
    }


def annotate_by_motivo(
    cases: list[dict],
    current_motivo: str | None,
) -> list[tuple[dict, str | None, str | None]]:
    """Anota cada precedente segun comparta patron de motivo con el caso actual.

    Devuelve (caso, etiqueta, origen) con los que matchean primero. `origen` dice
    si el match vino del campo motivo o de las observaciones: un match indirecto
    vale menos y conviene poder decirlo.

    Fuente unica del criterio: la formateaban por separado el prompt de precedentes
    y el resumen deterministico, con riesgo de divergir.
    """
    anotados = []
    for c in cases:
        texto_completo = f"{c.get('motivo', '')} {c.get('observations', '')}"
        etiqueta = motivo_match_label(current_motivo, texto_completo) if current_motivo else None
        origen = None
        if etiqueta and current_motivo:
            directo = motivo_match_label(current_motivo, c.get("motivo", ""))
            origen = "motivo" if directo else "observaciones"
        anotados.append((c, etiqueta, origen))

    anotados.sort(key=lambda x: (x[1] is None,))
    return anotados


def format_policies_for_prompt(policies: list[dict]) -> str:
    """Format policies as numbered Markdown sections for LLM context."""
    if not policies:
        return "(No se encontraron politicas relevantes)"
    lines = []
    for i, p in enumerate(policies, 1):
        score_pct = int(p.get("score", 0) * 100)
        lines.append(f"### Politica {i} (relevancia: {score_pct}%)")
        lines.append(f"- Codigo: {p.get('code', DISPLAY_FALLBACK)}")
        lines.append(f"- Categoria: {p.get('category', DISPLAY_FALLBACK)}")
        lines.append(f"- Nombre: {p.get('name', DISPLAY_FALLBACK)}")
        lines.append(f"- Descripcion: {p.get('description', DISPLAY_FALLBACK)}")
        lines.append(f"- Referencia: {p.get('reference', DISPLAY_FALLBACK)}")
        lines.append("")
    return "\n".join(lines)


def format_cases_for_prompt(cases: list[dict], current_motivo: str | None = None) -> str:
    """Format historical cases as numbered sections for LLM context.

    If current_motivo is provided, cases with matching motivo are tagged
    [MOTIVO SIMILAR] and sorted first — deterministic, no LLM needed.
    """
    if not cases:
        return f"(No se encontraron precedentes similares con similitud >= {SIMILAR_CASES_SCORE_THRESHOLD})"

    lines = []
    for i, (c, etiqueta, _origen) in enumerate(annotate_by_motivo(cases, current_motivo), 1):
        tag = " [MOTIVO SIMILAR]" if etiqueta else ""
        score_pct = int(c.get("score", 0) * 100)
        lines.append(f"### Precedente {i}{tag} (similitud: {score_pct}%)")
        lines.append(f"- Caso: {c.get('case_id', DISPLAY_FALLBACK)} | Motivo: {c.get('motivo', DISPLAY_FALLBACK)}")
        lines.append(
            f"- Comercio: {c.get('merchant', DISPLAY_FALLBACK)} | "
            f"Monto: USD {c.get('amount_usd', 0):.2f} | "
            f"Pais: {c.get('country', DISPLAY_FALLBACK)}"
        )
        lines.append(
            f"- Resolucion: {c.get('resolution', DISPLAY_FALLBACK)} "
            f"({c.get('resolution_days', '?')} dias)"
        )
        if c.get("observations"):
            lines.append(f"- Observaciones: {c['observations']}")
        lines.append("")
    return "\n".join(lines)
