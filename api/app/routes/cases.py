from fastapi import APIRouter, Depends, Query

from ..dependencies import get_retriever
from ..domain.constants import SIMILAR_CASES_TOP_K
from ..domain.models import BusquedaSemanticaResponse
from ..rag.formatter import envolver_resultados, format_cases_for_prompt
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
    top_k: int = SIMILAR_CASES_TOP_K,
    transaction_id: str = Query(
        "", description="La transaccion en analisis: sus propios casos se excluyen",
    ),
    retriever: QdrantRetriever = Depends(get_retriever),
) -> BusquedaSemanticaResponse:
    """Semantic search over 'historical_cases' collection.
    Used by n8n AI Agent as 'find_similar_cases' tool.

    `transaction_id` es opcional por compatibilidad, pero el orquestador lo manda
    siempre: sin el, los casos de la propia transaccion vuelven como precedentes
    de si mismos y se llevan los primeros puestos. Ver `retriever._query_cases`.
    """
    results = retriever.search_similar_cases(
        merchant=merchant,
        amount=amount,
        payment_method=payment_method,
        country=country,
        fraud_score=fraud_score,
        motivo=motivo,
        top_k=top_k,
        excluir_transaction_id=transaction_id,
    )
    return envolver_resultados(results, format_cases_for_prompt(results))
