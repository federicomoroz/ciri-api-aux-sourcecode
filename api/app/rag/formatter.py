"""
Prompt formatters for RAG results.

Single source of truth for formatting policies and cases into LLM-readable text.
Extracted from QdrantRetriever to keep retrieval and presentation concerns separate.
"""

from ..domain.constants import DISPLAY_FALLBACK, SIMILAR_CASES_SCORE_THRESHOLD


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


def format_cases_for_prompt(cases: list[dict]) -> str:
    """Format historical cases as numbered sections for LLM context."""
    if not cases:
        return f"(No se encontraron precedentes similares con similitud >= {SIMILAR_CASES_SCORE_THRESHOLD})"
    lines = []
    for i, c in enumerate(cases, 1):
        score_pct = int(c.get("score", 0) * 100)
        lines.append(f"### Precedente {i} (similitud: {score_pct}%)")
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
