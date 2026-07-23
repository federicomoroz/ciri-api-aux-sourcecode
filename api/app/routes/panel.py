"""
Test panel routes.

GET  /panel                    — serves the interactive test panel HTML page
GET  /api/panel/n8n-status     — liveness check for n8n (used by panel UI badge)
POST /api/panel/analyze        — runs analysis via n8n (or direct pipeline fallback)
"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from ..config import Settings
from ..dependencies import (
    get_pipeline_service,
    get_report_generator,
    get_settings,
)
from ..domain.constants import (
    N8N_HEALTHZ_PATH,
    N8N_PING_TIMEOUT_S,
    N8N_TIMEOUT_S,
    N8N_WEBHOOK_PATH,
    N8N_WEBHOOK_TEST_PATH,
)
from ..domain.models import AnalyzeRequest
from ..reports.generator import ReportGenerator
from ..services.pipeline import PipelineService

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

    status_base = {
        "url": base,
        "webhook_url": base + N8N_WEBHOOK_PATH,
        "webhook_test_url": base + N8N_WEBHOOK_TEST_PATH,
        **form_urls,
    }
    try:
        async with httpx.AsyncClient(timeout=N8N_PING_TIMEOUT_S) as client:
            r = await client.get(url)
        return {"available": r.status_code < 500, **status_base}
    except httpx.HTTPError as e:
        logger.info("n8n ping failed: %s", e)
        return {"available": False, **status_base}


@router.post("/api/panel/analyze", response_class=HTMLResponse)
async def panel_analyze(
    req: AnalyzeRequest,
    direct: bool              = Query(False, description="Skip n8n, use direct FastAPI pipeline"),
    n8n_test: bool            = Query(False, description="Use n8n test webhook URL instead of production"),
    timeout_s: float          = Query(N8N_TIMEOUT_S, description="n8n webhook timeout in seconds", ge=10, le=600),
    pipeline: PipelineService = Depends(get_pipeline_service),
    report_gen: ReportGenerator = Depends(get_report_generator),
    settings: Settings        = Depends(get_settings),
) -> HTMLResponse:
    """
    Run the chargeback analysis pipeline and return an HTML report.

    Strategy:
    1. If direct=false, try the n8n explicit workflow (POST /webhook/chargeback-agent).
       n8n returns raw JSON data; the panel applies the HTML template locally.
    2. If n8n is unavailable or direct=true, use the direct FastAPI pipeline.
    """
    if not direct:
        html = await _try_n8n(req, settings, report_gen, n8n_test, timeout_s)
        if html is not None:
            return HTMLResponse(content=html, status_code=200)

    # ── Direct pipeline fallback ──────────────────────────────────────────
    try:
        html, usage = pipeline.run(req, model_name=settings.llm_model)
        response = HTMLResponse(content=html, status_code=200)
        response.headers["X-Usage-JSON"] = json.dumps(usage)
        return response
    except Exception as exc:
        logger.error("Direct pipeline failed for %s: %s", req.transaction_id, exc, exc_info=True)
        error_html = (
            f"<html><body style='font-family:monospace;padding:2em'>"
            f"<h2>Pipeline Error</h2>"
            f"<p><b>Transaction:</b> {req.transaction_id}</p>"
            f"<p>An internal error occurred. Check server logs for details.</p>"
            f"</body></html>"
        )
        return HTMLResponse(content=error_html, status_code=500)


async def _try_n8n(
    req: AnalyzeRequest,
    settings: Settings,
    report_gen: ReportGenerator,
    n8n_test: bool,
    timeout_s: float = N8N_TIMEOUT_S,
) -> str | None:
    """Try n8n webhook. Returns rendered HTML on success, None on failure.

    n8n responds with raw JSON data (no HTML). The panel applies the
    HTML template via ReportGenerator to produce the formatted report.
    """
    webhook_path = N8N_WEBHOOK_TEST_PATH if n8n_test else N8N_WEBHOOK_PATH
    n8n_url = settings.n8n_base_url.rstrip("/") + webhook_path
    logger.info("panel: posting to n8n %s at %s for %s (timeout=%ss)", "TEST" if n8n_test else "PROD", n8n_url, req.transaction_id, timeout_s)
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                n8n_url,
                json={
                    "transaction_id": req.transaction_id,
                    "motivo": req.motivo,
                    "cliente_vip": req.cliente_vip,
                },
            )
        if r.status_code != 200:
            logger.warning("panel: n8n returned status=%s — falling back to direct", r.status_code)
            return None

        content_type = r.headers.get("content-type", "")

        # JSON response: raw data from n8n → apply HTML template locally
        if "application/json" in content_type:
            data = r.json()
            # Cache hit: n8n returns {cached: true, html: "..."} directly
            if data.get("cached") and data.get("html"):
                logger.info("panel: n8n cache hit for %s", req.transaction_id)
                return data["html"]
            # Normal: render HTML from raw data
            logger.info("panel: n8n returned raw data for %s — rendering HTML", req.transaction_id)
            return report_gen.render(data)

        # Legacy: text/html response (backwards compatible)
        if "text/html" in content_type:
            logger.info("panel: n8n returned HTML for %s", req.transaction_id)
            return r.text

        logger.warning("panel: n8n unexpected content-type=%s — falling back to direct", content_type)
    except Exception as exc:
        logger.warning("panel: n8n unreachable at %s (%s) — falling back to direct", n8n_url, exc)
    return None
