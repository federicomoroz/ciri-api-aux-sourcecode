"""
Prompt formatters for RAG results.

Single source of truth for formatting policies and cases into LLM-readable text.
Extracted from QdrantRetriever to keep retrieval and presentation concerns separate.
"""

import logging

from ..domain.constants import SIMILAR_CASES_SCORE_THRESHOLD

logger = logging.getLogger(__name__)


def format_policies_for_prompt(policies: list[dict]) -> str:
    """Format policies as numbered Markdown sections for LLM context."""
    if not policies:
        return "(No se encontraron politicas relevantes)"
    lines = []
    for i, p in enumerate(policies, 1):
        score_pct = int(p.get("score", 0) * 100)
        lines.append(f"### Politica {i} (relevancia: {score_pct}%)")
        lines.append(f"- Codigo: {p.get('code', 'N/A')}")
        lines.append(f"- Categoria: {p.get('category', 'N/A')}")
        lines.append(f"- Nombre: {p.get('name', 'N/A')}")
        lines.append(f"- Descripcion: {p.get('description', 'N/A')}")
        lines.append(f"- Referencia: {p.get('reference', 'N/A')}")
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
        lines.append(f"- Caso: {c.get('case_id', 'N/A')} | Motivo: {c.get('motivo', 'N/A')}")
        lines.append(
            f"- Comercio: {c.get('merchant', 'N/A')} | "
            f"Monto: USD {c.get('amount_usd', 0):.2f} | "
            f"Pais: {c.get('country', 'N/A')}"
        )
        lines.append(
            f"- Resolucion: {c.get('resolution', 'N/A')} "
            f"({c.get('resolution_days', '?')} dias)"
        )
        if c.get("observations"):
            lines.append(f"- Observaciones: {c['observations']}")
        lines.append("")
    return "\n".join(lines)
