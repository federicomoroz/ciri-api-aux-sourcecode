"""
Test panel routes.

GET  /panel                    — serves the interactive test panel HTML page
GET  /api/panel/n8n-status     — liveness check for n8n (used by panel UI badge)
POST /api/panel/analyze        — runs analysis via n8n (or direct pipeline fallback)
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from ..analysis.analyzer import Analyzer
from ..config import Settings
from ..data.db import Database
from ..dependencies import (
    get_analyzer,
    get_db,
    get_report_generator,
    get_resolution_service,
    get_retriever,
    get_settings,
)
from ..domain.constants import (
    N8N_HEALTHZ_PATH,
    N8N_PING_TIMEOUT_S,
    N8N_TIMEOUT_S,
    N8N_WEBHOOK_PATH,
)
from ..domain.models import AnalyzeRequest
from ..rag.retriever import QdrantRetriever
from ..reports.generator import ReportGenerator
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["panel"])


@router.get("/panel", response_class=HTMLResponse, include_in_schema=False)
def serve_panel(
    report_gen: ReportGenerator = Depends(get_report_generator),
) -> HTMLResponse:
    """Serve the interactive test panel page."""
    tmpl = report_gen.env.get_template("test_panel.html")
    return HTMLResponse(content=tmpl.render(), status_code=200)


# ─── Panel analyze endpoint ───────────────────────────────────────────────────


@router.get("/api/panel/n8n-status")
async def n8n_status(settings: Settings = Depends(get_settings)) -> dict:
    """Quick liveness check for n8n — used by the panel UI to show a status badge."""
    url = settings.n8n_base_url.rstrip("/") + N8N_HEALTHZ_PATH
    try:
        async with httpx.AsyncClient(timeout=N8N_PING_TIMEOUT_S) as client:
            r = await client.get(url)
        return {"available": r.status_code < 500, "url": settings.n8n_base_url}
    except httpx.HTTPError as e:
        logger.debug("n8n ping failed: %s", e)
        return {"available": False, "url": settings.n8n_base_url}


@router.post("/api/panel/analyze", response_class=HTMLResponse)
async def panel_analyze(
    req: AnalyzeRequest,
    db: Database                      = Depends(get_db),
    retriever: QdrantRetriever        = Depends(get_retriever),
    analyzer: Analyzer                = Depends(get_analyzer),
    resolution_svc: ResolutionService = Depends(get_resolution_service),
    report_gen: ReportGenerator       = Depends(get_report_generator),
    settings: Settings                = Depends(get_settings),
) -> HTMLResponse:
    """
    Run the chargeback analysis pipeline and return an HTML report.

    Strategy:
    1. Try the n8n explicit workflow (POST /webhook/chargeback-agent).
       n8n orchestrates 9 explicit HTTP calls to FastAPI and returns text/html.
    2. If n8n is unavailable or returns a non-HTML response, fall back to the
       direct FastAPI pipeline (identical business logic, no n8n dependency).
    """
    # ── Primary: n8n explicit workflow ────────────────────────────────────────
    n8n_url = settings.n8n_base_url.rstrip("/") + N8N_WEBHOOK_PATH
    try:
        async with httpx.AsyncClient(timeout=N8N_TIMEOUT_S) as client:
            r = await client.post(
                n8n_url,
                json={
                    "transaction_id": req.transaction_id,
                    "motivo":         req.motivo,
                    "cliente_vip":    req.cliente_vip,
                },
            )
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            logger.info("panel: n8n returned HTML for %s", req.transaction_id)
            return HTMLResponse(content=r.text, status_code=200)
        logger.warning(
            "panel: n8n returned status=%s content-type=%s — falling back to direct pipeline",
            r.status_code,
            r.headers.get("content-type", ""),
        )
    except Exception as exc:
        logger.warning("panel: n8n unreachable (%s) — falling back to direct pipeline", exc)

    # ── Fallback: direct FastAPI pipeline ────────────────────────────────────
    return await _direct_pipeline(req, db, retriever, analyzer, resolution_svc, report_gen)


# ─── Direct pipeline fallback ─────────────────────────────────────────────────

async def _direct_pipeline(
    req: AnalyzeRequest,
    db: Database,
    retriever: QdrantRetriever,
    analyzer: Analyzer,
    resolution_svc: ResolutionService,
    report_gen: ReportGenerator,
) -> HTMLResponse:
    """
    Run the full chargeback analysis pipeline without n8n.
    Mirrors exactly what the n8n explicit workflow does, calling each step in order.
    """
    txn_id = req.transaction_id

    # Step 1 — lookup_transaction
    tx = db.get_transaction(txn_id)
    if not tx:
        raise HTTPException(
            status_code=404,
            detail=f"Transaction {txn_id} not found in database.",
        )

    # Step 2 — get_logs
    logs = db.get_logs_for_transaction(txn_id)

    # Step 3 — search_policies
    policies = retriever.search_policies(
        motivo=req.motivo,
        channel=tx.get("channel", ""),
        payment_method=tx.get("payment_method", ""),
        fraud_score=int(tx.get("fraud_score", 0)),
        country=tx.get("country", ""),
    )

    # Step 4 — find_similar_cases
    similar_cases = retriever.search_similar_cases(
        merchant=tx.get("merchant", ""),
        amount=float(tx.get("amount_usd", 0)),
        payment_method=tx.get("payment_method", ""),
        country=tx.get("country", ""),
        fraud_score=int(tx.get("fraud_score", 0)),
        motivo=req.motivo,
    )

    # Step 5 — merchant_risk
    merchant_risk = analyzer.merchant_risk_profile(tx.get("merchant", ""))

    # Step 6 — client history
    client_history = db.get_client_history(tx.get("client_id", ""))

    # Steps 7+8 — resolve + judge (via ResolutionService)
    resolution = resolution_svc.resolve(
        tx_data=tx,
        policies=policies,
        similar_cases=similar_cases,
        logs=logs,
        merchant_risk=merchant_risk,
        client_history=client_history,
        motivo=req.motivo,
        cliente_vip=req.cliente_vip,
    )

    judge = resolution_svc.judge(
        resolution=resolution,
        full_context={
            "transaction":    tx,
            "motivo":         req.motivo,
            "policies":       policies,
            "similar_cases":  similar_cases,
        },
    )

    # Step 9 — html report
    report_data = {
        "transaction":        tx,
        "resolution":         resolution,
        "judge_evaluation":   judge,
        "agent_analysis":     (
            f"Pipeline directo — {txn_id}: {tx.get('merchant','')}, "
            f"USD {tx.get('amount_usd','')}, canal {tx.get('channel','')}, "
            f"país {tx.get('country','')}, score fraude {tx.get('fraud_score','')}."
        ),
        "merchant_risk":      merchant_risk,
        "client_profile":     client_history,
        "logs":               logs,
        "policies_evaluated": policies,
        "similar_cases":      similar_cases,
        "hitl_decision":      None,
        "cache_hit":          False,
        "guardrail_warnings": resolution.get("guardrail_warnings", []),
    }

    html = report_gen.render(report_data)
    return HTMLResponse(content=html, status_code=200)
