from fastapi import APIRouter, Depends, Query

from ..dependencies import get_retriever
from ..rag.retriever import QdrantRetriever

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("/similar")
def find_similar_cases(
    merchant: str = Query(...),
    amount: float = Query(...),
    payment_method: str = Query(...),
    country: str = Query(...),
    fraud_score: int = Query(...),
    motivo: str | None = None,
    top_k: int = 5,
    retriever: QdrantRetriever = Depends(get_retriever),
) -> dict:
    """Semantic search over 'historical_cases' collection.
    Used by n8n AI Agent as 'find_similar_cases' tool."""
    results = retriever.search_similar_cases(
        merchant=merchant,
        amount=amount,
        payment_method=payment_method,
        country=country,
        fraud_score=fraud_score,
        motivo=motivo,
        top_k=top_k,
    )
    formatted = retriever.format_cases_for_prompt(results)
    return {
        "query_used": results[0].get("_query", "") if results else "",
        "results": results,
        "formatted_for_llm": formatted,
        "count": len(results),
    }
