"""
Test panel routes.

GET  /panel                    — serves the interactive test panel HTML page
GET  /api/panel/n8n-status     — liveness check for n8n (used by panel UI badge)
POST /api/panel/analyze        — runs analysis via n8n (or direct pipeline fallback)
"""

import json
import logging
from contextlib import contextmanager

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..config import Settings
from ..dependencies import (
    get_pipeline_service,
    get_report_generator,
    get_settings,
)
from ..data.precomputados import USO_DEMO, casos_demo, informe_demo
from ..domain.constants import (
    LLM_CREDIT_EXHAUSTED_MARKER,
    N8N_HEALTHZ_PATH,
    N8N_PING_TIMEOUT_S,
    N8N_TIMEOUT_S,
    N8N_WEBHOOK_PATH,
    N8N_WEBHOOK_TEST_PATH,
)
from ..domain.models import AnalyzeRequest
from ..llm.client import AnthropicClient
from ..reports.generator import ReportGenerator
from ..services.pipeline import PipelineService
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["panel"])


@contextmanager
def _byok_pipeline(api_key: str, base: PipelineService, settings: Settings, request: Request):
    """Pipeline efimero con la API key del visitante.

    Los clientes se cierran al salir: son de un solo uso y cada uno abre su
    propio pool de conexiones. Sin esto, cada request con BYOK dejaba dos pools
    colgados hasta que pasara el recolector.
    """
    tracer = request.app.state.tracer
    llm = AnthropicClient(api_key=api_key, model=settings.llm_model, tracer=tracer)
    llm_res = (
        AnthropicClient(api_key=api_key, model=settings.llm_model_resolution, tracer=tracer)
        if settings.llm_model_resolution else llm
    )
    try:
        yield PipelineService(
            db=base.db, retriever=base.retriever, analyzer=base.analyzer,
            resolution_svc=ResolutionService(llm, tracer, llm_resolution=llm_res),
            report_gen=base.report_gen,
        )
    finally:
        llm.close()
        if llm_res is not llm:
            llm_res.close()


def _es_falta_de_saldo(exc: Exception) -> bool:
    return LLM_CREDIT_EXHAUSTED_MARKER in str(exc).lower()


def _hay_que_ahorrar(req: AnalyzeRequest, settings: Settings) -> bool:
    """Si corresponde servir el caso ya resuelto en vez de gastar en el modelo.

    Quien trae su propia clave paga lo suyo y corre el pipeline completo: el modo
    demo no le aplica.
    """
    return settings.demo_mode and not req.api_key


def _respuesta_demo(txn_id: str, settings: Settings) -> HTMLResponse | None:
    """El informe prearmado del caso. None si ese caso no tiene uno."""
    html = informe_demo(settings.demo_reports_path, txn_id)
    if html is None:
        return None

    logger.warning(
        "MODO DEMO: %s se responde con su informe prearmado. No se llamo al modelo "
        "y no se gasto nada. Con una API key propia el pipeline corre completo.",
        txn_id,
    )
    respuesta = HTMLResponse(content=html, status_code=200)
    respuesta.headers["X-Modo-Demo"] = "true"
    respuesta.headers["X-Usage-JSON"] = json.dumps(USO_DEMO)
    return respuesta


def _pagina_sin_caso_demo(txn_id: str, settings: Settings) -> str:
    """Un caso sin informe prearmado, en modo demo: se explica, no se inventa."""
    casos = ", ".join(f"<code>{c}</code>" for c in casos_demo(settings.demo_reports_path))
    return _pagina(
        "Este caso necesita una API key",
        f"<p>El servidor esta en <b>modo demo</b>: no llama al modelo, para que evaluar "
        f"el sistema no consuma la cuenta de nadie.</p>"
        f"<p><b>Dos formas de seguir:</b></p><ul>"
        f"<li>Los casos {casos} vienen con su analisis ya generado y se ven al instante.</li>"
        f"<li>Para investigar <code>{txn_id}</code> —o cualquier otro— cargá tu clave de "
        f"Anthropic en el campo <b>API key</b> del panel: ahí corre el pipeline completo, "
        f"con tu cuenta.</li></ul>"
        f"<p>Lo que no cuesta nada sigue disponible igual: consultar transacciones y logs, "
        f"la busqueda semantica de politicas y precedentes, el riesgo del comercio y el SLA.</p>",
        txn_id,
    )


def _pagina_de_error(txn_id: str, exc: Exception, settings: Settings) -> str:
    """La pagina cuando el analisis no pudo correr.

    Un 'error interno, revise los logs' no sirve: quien evalua no tiene los logs.
    """
    if _es_falta_de_saldo(exc):
        return _pagina(
            "Sin saldo en esa API key",
            "<p>La clave de Anthropic usada para este analisis se quedo sin credito.</p>"
            "<p>Con otra clave con saldo, el caso se investiga normalmente.</p>",
            txn_id,
        )
    return _pagina(
        "No se pudo completar el analisis",
        "<p>El pipeline se interrumpio. El detalle quedo en los logs del servidor, "
        "identificado por la transaccion.</p>",
        txn_id,
    )


def _pagina(titulo: str, cuerpo: str, txn_id: str) -> str:
    return (
        '<html><body style="font-family:system-ui,sans-serif;max-width:640px;'
        'margin:3em auto;padding:0 1.5em;line-height:1.6;color:#1a1a1a">'
        f'<h2 style="margin-bottom:.2em">{titulo}</h2>'
        f'<p style="color:#666;margin-top:0">Transaccion: <code>{txn_id}</code></p>'
        f"{cuerpo}</body></html>"
    )


def _correr_directo(pipeline: PipelineService, req: AnalyzeRequest, settings: Settings) -> HTMLResponse:
    """Ejecuta el pipeline directo y devuelve el informe, o una pagina de error."""
    try:
        html, usage = pipeline.run(req, model_name=settings.llm_model)
        response = HTMLResponse(content=html, status_code=200)
        response.headers["X-Usage-JSON"] = json.dumps(usage)
        return response
    except Exception as exc:
        logger.error("Direct pipeline failed for %s: %s", req.transaction_id, exc, exc_info=True)
        return HTMLResponse(
            content=_pagina_de_error(req.transaction_id, exc, settings),
            status_code=500,
        )


def _sse(step: str, data: dict) -> str:
    return f"data: {json.dumps({'step': step, **data}, ensure_ascii=False)}\n\n"


def _emitir(pipeline: PipelineService, req: AnalyzeRequest, settings: Settings):
    """Traduce los eventos del pipeline a lineas SSE.

    En modo demo no se ejecuta nada: el caso prearmado sale directo, y el panel
    lo muestra igual que un analisis real pero con su cartel.
    """
    if _hay_que_ahorrar(req, settings):
        yield from _emitir_demo(req, settings)
        return
    try:
        for step, data in pipeline.run_streaming(req, model_name=settings.llm_model):
            yield _sse(step, data)
    except Exception as exc:
        logger.error("Pipeline SSE fallo para %s: %s", req.transaction_id, exc, exc_info=True)
        mensaje = (
            "La API key usada se quedo sin saldo. Con otra clave con credito, el caso "
            "se investiga normalmente."
            if _es_falta_de_saldo(exc)
            else "El analisis se interrumpio. Revisar los logs del servidor."
        )
        yield _sse("error", {"message": mensaje})


def _emitir_demo(req: AnalyzeRequest, settings: Settings):
    """El caso prearmado, anunciado como tal desde el primer evento."""
    yield _sse("start", {"transaction_id": req.transaction_id, "demo": True})
    html = informe_demo(settings.demo_reports_path, req.transaction_id)
    if html is None:
        casos = ", ".join(casos_demo(settings.demo_reports_path))
        yield _sse("error", {"message": (
            f"Modo demo: solo {casos} vienen con su analisis ya generado. Para investigar "
            f"{req.transaction_id} cargá tu clave de Anthropic en el campo 'API key' y el "
            "pipeline corre completo."
        )})
        return
    logger.warning(
        "MODO DEMO: %s se responde con su informe prearmado. No se llamo al modelo.",
        req.transaction_id,
    )
    yield _sse("done", {"html": html, "usage": USO_DEMO})


@router.get("/panel", response_class=HTMLResponse, include_in_schema=False)
def serve_panel(
    report_gen: ReportGenerator = Depends(get_report_generator),
) -> HTMLResponse:
    """Serve the interactive test panel page."""
    tmpl = report_gen.env.get_template("test_panel.html")
    return HTMLResponse(content=tmpl.render(), status_code=200)


@router.get("/api/panel/server-key-status")
def server_key_status(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Check if the server has its own Anthropic API key configured.

    When True, BYOK is optional — the panel can run analyses using the server key.
    When False, visitors must provide their own key (BYOK required).
    """
    return JSONResponse({"has_server_key": bool(settings.anthropic_api_key)})


# ─── Panel analyze endpoint ───────────────────────────────────────────────────


@router.get("/api/panel/n8n-status")
async def n8n_status(settings: Settings = Depends(get_settings)) -> dict:
    """Quick liveness check for n8n — used by the panel UI to show a status badge.

    Without CB_N8N_BASE_URL there is no instance to point at: the panel asks the
    user for one instead of advertising a URL that leads nowhere.
    """
    base = settings.n8n_base_url.rstrip("/")
    if not base:
        return {"configured": False, "available": False}
    url = base + N8N_HEALTHZ_PATH
    # Derive form URLs only when configured
    form_urls: dict[str, str] = {}
    if settings.n8n_form_path:
        fp = settings.n8n_form_path if settings.n8n_form_path.startswith("/") else "/" + settings.n8n_form_path
        form_urls["form_url"] = base + fp
        form_urls["form_test_url"] = base + fp.replace("/form/", "/form-test/", 1)

    status_base = {
        "configured": True,
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
    request: Request,
    direct: bool              = Query(False, description="Skip n8n, use direct FastAPI pipeline"),
    n8n_test: bool            = Query(False, description="Use n8n test webhook URL instead of production"),
    n8n_base_url: str | None  = Query(None, description="Override n8n base URL (evaluator's own instance)"),
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
    # Modo demo: el caso ya resuelto sale sin pasar por el modelo ni por n8n.
    if _hay_que_ahorrar(req, settings):
        demo = _respuesta_demo(req.transaction_id, settings)
        if demo is not None:
            return demo
        return HTMLResponse(
            content=_pagina_sin_caso_demo(req.transaction_id, settings), status_code=200
        )

    if not direct:
        html = await _try_n8n(req, settings, report_gen, n8n_test, timeout_s, n8n_base_url)
        if html is not None:
            return HTMLResponse(content=html, status_code=200)

    # ── Direct pipeline fallback ──────────────────────────────────────────
    if req.api_key:
        with _byok_pipeline(req.api_key, pipeline, settings, request) as propio:
            return _correr_directo(propio, req, settings)
    if not settings.anthropic_api_key:
        return HTMLResponse(
            content="<html><body><h2>API key requerida</h2><p>Ingresa tu Anthropic API Key en el panel.</p></body></html>",
            status_code=400,
        )
    return _correr_directo(pipeline, req, settings)


@router.post("/api/panel/analyze-stream")
def panel_analyze_stream(
    req: AnalyzeRequest,
    request: Request,
    pipeline: PipelineService = Depends(get_pipeline_service),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Run the pipeline with SSE streaming — emits real-time progress events.

    BYOK: visitors must provide their own Anthropic API key.
    The server key is reserved for n8n and direct API endpoints.
    """
    if not req.api_key and not settings.anthropic_api_key:
        return StreamingResponse(
            iter([f"data: {json.dumps({'step': 'error', 'message': 'API key requerida. Ingresa tu Anthropic API Key.'})}\n\n"]),
            media_type="text/event-stream",
        )

    def generate():
        # El pipeline BYOK vive dentro del generador: la respuesta se consume
        # despues de que esta funcion retorna, asi que cerrarlo antes dejaria
        # al stream sin cliente con el que trabajar.
        try:
            if req.api_key:
                with _byok_pipeline(req.api_key, pipeline, settings, request) as propio:
                    yield from _emitir(propio, req, settings)
            else:
                yield from _emitir(pipeline, req, settings)
        except Exception as exc:
            logger.error("Streaming pipeline failed: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'step': 'error', 'message': 'Error interno del pipeline. Revisa que tu API key sea valida.'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _try_n8n(
    req: AnalyzeRequest,
    settings: Settings,
    report_gen: ReportGenerator,
    n8n_test: bool,
    timeout_s: float = N8N_TIMEOUT_S,
    n8n_base_url_override: str | None = None,
) -> str | None:
    """Try n8n webhook. Returns rendered HTML on success, None on failure.

    n8n responds with raw JSON data (no HTML). The panel applies the
    HTML template via ReportGenerator to produce the formatted report.
    """
    base = (n8n_base_url_override or settings.n8n_base_url).rstrip("/")
    if not base:
        logger.info("panel: sin instancia de n8n configurada, se usa el pipeline directo")
        return None
    webhook_path = N8N_WEBHOOK_TEST_PATH if n8n_test else N8N_WEBHOOK_PATH
    n8n_url = base + webhook_path
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
