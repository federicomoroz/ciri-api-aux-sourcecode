"""
Prompt formatters for RAG results.

Single source of truth for formatting policies and cases into LLM-readable text.
Extracted from QdrantRetriever to keep retrieval and presentation concerns separate.
"""

from ..domain.constants import DISPLAY_FALLBACK, SIMILAR_CASES_SCORE_THRESHOLD

# Synonym groups for mechanical motivo matching.
# Each group is a set of keywords — if the current motivo AND a precedent's
# motivo/observations both contain a keyword from the SAME group, it's a match.
_MOTIVO_SYNONYM_GROUPS: list[set[str]] = [
    {"duplicado", "duplicada", "doble", "doble cobro", "doble cargo", "cargo doble"},
    {"no reconoce", "no reconocida", "no autorizado", "no autorizada", "fraude"},
    {"no recibido", "no entregado", "no entrega", "falta entrega", "no llego"},
    {"defecto", "defectuoso", "calidad", "dañado", "roto"},
    {"monto incorrecto", "monto erroneo", "cobro incorrecto"},
    {"cancelado", "cancelacion", "post-cancelacion", "post cancelacion"},
]


def _motivo_matches(motivo_a: str, motivo_b: str) -> bool:
    """Check if two motivos share a synonym group keyword. Pure string matching."""
    a = motivo_a.lower()
    b = motivo_b.lower()
    for group in _MOTIVO_SYNONYM_GROUPS:
        a_match = any(kw in a for kw in group)
        b_match = any(kw in b for kw in group)
        if a_match and b_match:
            return True
    return False


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

    # Annotate matches deterministically.
    annotated = []
    for c in cases:
        case_text = f"{c.get('motivo', '')} {c.get('observations', '')}"
        is_match = bool(current_motivo) and _motivo_matches(current_motivo, case_text)
        annotated.append((c, is_match))

    # Sort: matches first, then by original order.
    annotated.sort(key=lambda x: (not x[1],))

    lines = []
    for i, (c, is_match) in enumerate(annotated, 1):
        tag = " [MOTIVO SIMILAR]" if is_match else ""
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
