from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..data.db import Database
from ..dependencies import get_db, get_retriever, get_updater
from ..domain.models import PolicyCreate, PolicyUpdate
from ..rag.retriever import QdrantRetriever
from ..rag.updater import RAGUpdater

router = APIRouter(prefix="/api/policies", tags=["policies"])


@router.get("/search")
def search_policies(
    q: str = Query(default="", description="Free-text semantic search query"),
    motivo: str | None = None,
    channel: str | None = None,
    payment_method: str | None = None,
    fraud_score: int | None = None,
    country: str | None = None,
    retriever: QdrantRetriever = Depends(get_retriever),
) -> dict:
    """Semantic search over Qdrant 'policies' collection.
    Used by n8n AI Agent as 'search_policies' tool."""
    results = retriever.search_policies(
        motivo=motivo,
        channel=channel or "",
        payment_method=payment_method or "",
        fraud_score=fraud_score or 50,
        country=country or "",
    )
    formatted = retriever.format_policies_for_prompt(results)
    return {
        "query_used": results[0].get("_query", q) if results else q,
        "results": results,
        "formatted_for_llm": formatted,
        "count": len(results),
    }


@router.get("/")
def list_policies(db: Database = Depends(get_db)) -> list[dict]:
    """List all policies from SQLite."""
    return db.get_all_policies()


@router.get("/{code}")
def get_policy(code: str, db: Database = Depends(get_db)) -> dict:
    """Get one policy by code."""
    policy = db.get_policy(code)
    if not policy:
        raise HTTPException(status_code=404, detail=f"Policy {code} not found")
    return policy


@router.post("/", status_code=201)
def create_policy(
    policy: PolicyCreate,
    db: Database = Depends(get_db),
    updater: RAGUpdater = Depends(get_updater),
) -> dict:
    """Create new policy → save to SQLite + index in Qdrant immediately."""
    now = datetime.now(timezone.utc).isoformat()
    policy_dict = {
        "code": policy.code,
        "name": policy.name,
        "category": policy.category,
        "description": policy.description,
        "reference": policy.reference,
        "created_at": now,
        "updated_at": now,
    }
    db.upsert_policy(policy_dict)
    updater.on_policy_created(policy_dict)
    return db.get_policy(policy.code)


@router.put("/{code}")
def update_policy(
    code: str,
    policy: PolicyUpdate,
    db: Database = Depends(get_db),
    updater: RAGUpdater = Depends(get_updater),
) -> dict:
    """Update policy → save to SQLite + re-index in Qdrant immediately.
    No redeploy needed — policies are DATA, not CODE."""
    existing = db.get_policy(code)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Policy {code} not found")

    updated = {**existing}
    if policy.name is not None:
        updated["name"] = policy.name
    if policy.category is not None:
        updated["category"] = policy.category
    if policy.description is not None:
        updated["description"] = policy.description
    if policy.reference is not None:
        updated["reference"] = policy.reference
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()

    db.upsert_policy(updated)
    updater.on_policy_updated(updated)
    return db.get_policy(code)


@router.delete("/{code}", status_code=204)
def delete_policy(
    code: str,
    db: Database = Depends(get_db),
    updater: RAGUpdater = Depends(get_updater),
) -> None:
    """Delete from SQLite + remove from Qdrant."""
    if not db.get_policy(code):
        raise HTTPException(status_code=404, detail=f"Policy {code} not found")
    db.delete_policy(code)
    updater.on_policy_deleted(code)
