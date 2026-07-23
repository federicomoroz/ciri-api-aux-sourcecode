"""
Test panel routes.

GET  /panel                    — serves the interactive test panel HTML page
GET  /api/panel/n8n-status     — liveness check for n8n (used by panel UI badge)
POST /api/panel/analyze        — runs analysis via n8n (or direct pipeline fallback)
"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
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
    LLM_PRICING,
    LLM_PRICING_PER_MTOK,
    N8N_HEALTHZ_PATH,
    N8N_PING_TIMEOUT_S,
    N8N_TIMEOUT_S,
    N8N_WEBHOOK_PATH,
    N8N_WEBHOOK_TEST_PATH,
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
    base = settings.n8n_base_url.rstrip("/")
    # Derive form URLs only when configured
    form_urls: dict[str, str] = {}
    if settings.n8n_form_path:
        fp = settings.n8n_form_path if settings.n8n_form_path.startswith("/") else "/" + settings.n8n_form_path
        form_urls["form_url"] = base + fp
        form_urls["form_test_url"] = base + fp.replace("/form/", "/form-test/", 1)

    try:
        async with httpx.AsyncClient(timeout=N8N_PING_TIMEOUT_S) as client:
            r = await client.get(url)
        return {
            "available": r.status_code < 500,
            "url": base,
            "webhook_url": base + N8N_WEBHOOK_PATH,
            "webhook_test_url": base + N8N_WEBHOOK_TEST_PATH,
            **form_urls,
        }
    except httpx.HTTPError as e:
        logger.debug("n8n ping failed: %s", e)
        return {
            "available": False,
            "url": base,
            "webhook_url": base + N8N_WEBHOOK_PATH,
            "webhook_test_url": base + N8N_WEBHOOK_TEST_PATH,
            **form_urls,
        }


@router.post("/api/panel/analyze", response_class=HTMLResponse)
async def panel_analyze(
    req: AnalyzeRequest,
    direct: bool                      = Query(False, description="Skip n8n, use direct FastAPI pipeline"),
    n8n_test: bool                    = Query(False, description="Use n8n test webhook URL instead of production"),
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
    1. If direct=false, try the n8n explicit workflow (POST /webhook/chargeback-agent).
       n8n orchestrates 9 explicit HTTP calls to FastAPI and returns text/html.
    2. If n8n is unavailable, returns non-HTML, or direct=true, use the
       direct FastAPI pipeline (identical business logic, no n8n dependency).
    """
    if direct:
        logger.info("panel: direct mode requested for %s", req.transaction_id)
    else:
        # ── Primary: n8n explicit workflow ────────────────────────────────────
        webhook_path = N8N_WEBHOOK_TEST_PATH if n8n_test else N8N_WEBHOOK_PATH
        n8n_url = settings.n8n_base_url.rstrip("/") + webhook_path
        logger.info("panel: posting to n8n %s for %s", "TEST" if n8n_test else "PROD", req.transaction_id)
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
    try:
        return await _direct_pipeline(
            req, db, retriever, analyzer, resolution_svc, report_gen,
            model_name=settings.llm_model,
        )
    except Exception as exc:
        logger.error("Direct pipeline failed for %s: %s", req.transaction_id, exc, exc_info=True)
        error_html = (
            f"<html><body style='font-family:monospace;padding:2em'>"
            f"<h2>Pipeline Error</h2>"
            f"<p><b>Transaction:</b> {req.transaction_id}</p>"
            f"<p><b>Error:</b> {type(exc).__name__}: {exc}</p>"
            f"</body></html>"
        )
        return HTMLResponse(content=error_html, status_code=200)


# ─── Direct pipeline fallback ─────────────────────────────────────────────────

async def _direct_pipeline(
    req: AnalyzeRequest,
    db: Database,
    retriever: QdrantRetriever,
    analyzer: Analyzer,
    resolution_svc: ResolutionService,
    report_gen: ReportGenerator,
    model_name: str = "",
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
    response = HTMLResponse(content=html, status_code=200)

    # Aggregate LLM usage from resolve (2 calls) + judge (1 call)
    resolve_usage = resolution.get("_usage", {})
    judge_usage = judge.get("_usage", {})
    total_in = resolve_usage.get("input_tokens", 0) + judge_usage.get("input_tokens", 0)
    total_out = resolve_usage.get("output_tokens", 0) + judge_usage.get("output_tokens", 0)
    total_calls = resolve_usage.get("call_count", 0) + judge_usage.get("call_count", 0)

    cost_usd = 0.0
    for key, (in_rate, out_rate) in LLM_PRICING.items():
        if key in model_name.lower():
            cost_usd = total_in * in_rate / LLM_PRICING_PER_MTOK + total_out * out_rate / LLM_PRICING_PER_MTOK
            break

    response.headers["X-Usage-JSON"] = json.dumps({
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "call_count": total_calls,
        "cost_usd": round(cost_usd, 6),
        "model": model_name,
    })
    return response
