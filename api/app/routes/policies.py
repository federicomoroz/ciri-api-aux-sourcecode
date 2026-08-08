import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from ..data.db import Database
from ..dependencies import get_db, get_retriever, get_updater
from ..domain.constants import FRAUD_SCORE_DEFAULT
from ..domain.models import PolicyCreate, PolicyUpdate
from ..rag.formatter import envolver_resultados, format_policies_for_prompt
from ..rag.retriever import QdrantRetriever
from ..rag.updater import RAGUpdater

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policies", tags=["policies"])


def _politica_o_404(db: Database, code: str) -> dict:
    """La politica, o 404. Tres rutas necesitaban exactamente esto."""
    policy = db.get_policy(code)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy {code} not found")
    return policy


@router.get("/search")
def search_policies(
    q: str = Query(default="", description="Free-text semantic search query"),
    motivo: str | None = None,
    channel: str | None = None,
    payment_method: str | None = None,
    fraud_score: int | None = None,
    country: str | None = None,
    top_k: int | None = Query(
        default=None, ge=1,
        description="Cuantas politicas devolver. Por defecto, todas las indexadas.",
    ),
    retriever: QdrantRetriever = Depends(get_retriever),
) -> dict:
    """Semantic search over Qdrant 'policies' collection.
    Used by n8n AI Agent as 'search_policies' tool."""
    results = retriever.search_policies(
        motivo=motivo,
        channel=channel or "",
        payment_method=payment_method or "",
        fraud_score=fraud_score or FRAUD_SCORE_DEFAULT,
        country=country or "",
        top_k=top_k,
    )
    return envolver_resultados(results, format_policies_for_prompt(results), q)


@router.get("/")
def list_policies(db: Database = Depends(get_db)) -> list[dict]:
    """List all policies from SQLite."""
    return db.get_all_policies()


@router.get("/{code}")
def get_policy(code: str, db: Database = Depends(get_db)) -> dict:
    """Get one policy by code."""
    return _politica_o_404(db, code)


def _invalidar_informes(db: Database, code: str) -> None:
    """Tocar una politica deja viejos todos los informes guardados.

    Reindexar en Qdrant no alcanza: el informe se sirve del cache, que no sabe
    con que politicas se genero. Sin invalidar, editar una politica cambiaria el
    indice pero el mismo caso seguiria devolviendo el HTML anterior — la feature
    andaria sin verse, que desde afuera es lo mismo que no andar.

    Best-effort: no poder vaciar el cache no puede tumbar la edicion, que ya se
    guardo y ya se indexo.
    """
    try:
        borrados = db.clear_report_cache()
        if borrados:
            logger.info(
                "%s cambio: se vaciaron %d informes cacheados que ya no reflejan las politicas vigentes",
                code, borrados,
            )
    except Exception:
        logger.warning("No se pudo vaciar el cache tras tocar %s", code, exc_info=True)


@router.post("/", status_code=201)
def create_policy(
    policy: PolicyCreate,
    db: Database = Depends(get_db),
    updater: RAGUpdater = Depends(get_updater),
) -> dict:
    """Create new policy -> save to SQLite + index in Qdrant immediately."""
    policy_dict = db.create_policy_record(policy.model_dump())
    updater.on_policy_created(policy_dict)
    _invalidar_informes(db, policy_dict["code"])
    return policy_dict


@router.put("/{code}")
def update_policy(
    code: str,
    policy: PolicyUpdate,
    db: Database = Depends(get_db),
    updater: RAGUpdater = Depends(get_updater),
) -> dict:
    """Update policy -> save to SQLite + re-index in Qdrant immediately.
    No redeploy needed — policies are DATA, not CODE."""
    existing = _politica_o_404(db, code)
    updated = db.merge_policy_update(existing, policy.model_dump(exclude_unset=True))
    updater.on_policy_updated(updated)
    _invalidar_informes(db, code)
    return updated


@router.delete("/{code}", status_code=204)
def delete_policy(
    code: str,
    db: Database = Depends(get_db),
    updater: RAGUpdater = Depends(get_updater),
) -> None:
    """Delete from SQLite + remove from Qdrant."""
    _politica_o_404(db, code)
    db.delete_policy(code)
    updater.on_policy_deleted(code)
    _invalidar_informes(db, code)
