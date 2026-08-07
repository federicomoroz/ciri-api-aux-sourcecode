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
from ..data.precomputados import (
    USO_DEMO,
    casos_demo,
    informe_de_ejemplo,
    informe_demo,
)
from ..dependencies import (
    get_pipeline_service,
    get_report_generator,
    get_settings,
)
from ..domain.constants import (
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    LLM_BAD_KEY_MARKER,
    LLM_CREDIT_EXHAUSTED_MARKER,
    N8N_HEALTHZ_PATH,
    N8N_PING_TIMEOUT_S,
    N8N_TIMEOUT_S,
    N8N_WEBHOOK_PATH,
    N8N_WEBHOOK_TEST_PATH,
    PASO_JUEZ,
    PASO_POLITICAS,
    PASO_RESOLUCION,
    RISK_FRAUD_SEVERE,
)
from ..domain.models import AnalyzeRequest
from ..reports.generator import ReportGenerator
from ..services.pipeline import PipelineService
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["panel"])


@contextmanager
def _byok_pipeline(api_key: str, base: PipelineService, settings: Settings, request: Request):
    """Pipeline efimero con la API key del visitante.

    Respeta la eleccion de modelo por paso: quien trae su clave corre la misma
    configuracion que el servidor, con su credencial. Los clientes se cierran al
    salir — son de un solo uso y cada uno abre su propio pool de conexiones. Sin
    esto, cada request con BYOK dejaba pools colgados hasta el recolector.
    """
    tracer = request.app.state.tracer
    modelos = request.app.state.modelos_service
    clientes = {
        paso: modelos.cliente(paso, api_key=api_key)
        for paso in (PASO_POLITICAS, PASO_RESOLUCION, PASO_JUEZ)
    }
    try:
        yield PipelineService(
            db=base.db, retriever=base.retriever, analyzer=base.analyzer,
            resolution_svc=ResolutionService(
                clientes[PASO_POLITICAS], tracer,
                llm_resolution=clientes[PASO_RESOLUCION],
                llm_judge=clientes[PASO_JUEZ],
            ),
            report_gen=base.report_gen,
        )
    finally:
        for cliente in set(clientes.values()):
            cerrar = getattr(cliente, "close", None)
            if cerrar:
                cerrar()


def _es_falta_de_saldo(exc: Exception) -> bool:
    return LLM_CREDIT_EXHAUSTED_MARKER in str(exc).lower()


def _es_clave_invalida(exc: Exception) -> bool:
    """Pegar mal la clave es el error mas probable de quien trae la suya."""
    return LLM_BAD_KEY_MARKER in str(exc).lower()


def _en_modo_demo(req: AnalyzeRequest, settings: Settings) -> bool:
    """Si esta peticion tiene que resolverse sin llamar al modelo.

    Manda el toggle del panel; si la peticion no dice nada, decide el servidor.
    Nota: aca no interviene la API key. Pedir modo demo teniendo clave es
    legitimo — es justamente como se mira un caso sin gastar.
    """
    return settings.demo_mode if req.demo_mode is None else req.demo_mode


def _html_demo(txn_id: str, settings: Settings, db=None) -> str | None:
    """El informe del caso, o el del guardado mas cercano en riesgo.

    Nunca devuelve un error cuando hay algo que mostrar: si el caso pedido no
    tiene analisis guardado, sale el ejemplo mas parecido en riesgo, y el propio
    informe aclara de que transaccion es.
    """
    exacto = informe_demo(settings.demo_reports_path, txn_id)
    if exacto is not None:
        return exacto

    # El score antifraude sale de SQLite, que no cuesta nada consultar.
    pedida = {"id": txn_id}
    if db is not None:
        pedida = {**(db.get_transaction(txn_id) or {}), "id": txn_id}
    ejemplo = informe_de_ejemplo(settings.demo_reports_path, pedida)
    return ejemplo[0] if ejemplo else None


def _respuesta_demo(
    txn_id: str, settings: Settings, db=None, *, por_falta_de_saldo: bool = False
) -> HTMLResponse | None:
    """El informe prearmado del caso. None solo si no hay ninguno guardado.

    Los dos caminos que llegan aca no son lo mismo y el log los distingue: en
    modo demo no se llamo al modelo; en el respaldo se llamo y no habia saldo.
    """
    html = _html_demo(txn_id, settings, db)
    if html is None:
        return None

    if por_falta_de_saldo:
        logger.warning(
            "SIN SALDO: %s se responde con su informe prearmado. La API key usada no "
            "tiene credito, asi que el analisis no pudo correr.",
            txn_id,
        )
    else:
        logger.warning(
            "MODO DEMO: %s se responde con su informe prearmado. No se llamo al modelo "
            "y no se gasto nada. Con el modo demo apagado y una API key con saldo, el "
            "pipeline corre completo.",
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


def _pagina_sin_n8n(txn_id: str) -> str:
    """Eligieron ejecutar por n8n sin decir donde esta."""
    return _pagina(
        "Falta la URL de tu n8n",
        "<p>Elegiste ejecutar a traves de n8n, pero no hay ninguna instancia indicada. "
        "Este servidor no puede adivinar donde corre tu n8n: la URL la tenes que poner "
        "vos, en el campo <b>n8n URL</b> del panel.</p>"
        "<p>Si todavia no lo importaste, o solo queres ver el resultado, elegi el modo "
        "<b>Directo</b>: corre el mismo pipeline dentro de la API.</p>",
        txn_id,
    )


def _pagina_n8n_no_respondio(txn_id: str, base: str, es_prueba: bool) -> str:
    """n8n estaba indicado pero no contesto. No se disimula con el pipeline directo."""
    ruta = N8N_WEBHOOK_TEST_PATH if es_prueba else N8N_WEBHOOK_PATH
    extra = (
        "<li>En modo <b>prueba</b> hay que apretar <b>Execute workflow</b> en el editor "
        "justo antes: el webhook de test escucha una sola ejecucion.</li>"
        if es_prueba else
        "<li>En modo <b>produccion</b> el workflow tiene que estar <b>activo</b>.</li>"
    )
    return _pagina(
        "Tu n8n no respondio",
        f"<p>Se llamo a <code>{base}{ruta}</code> y no hubo respuesta util.</p>"
        f"<p><b>Que revisar:</b></p><ul>"
        f"<li>Que la URL sea la base de n8n, no la del editor "
        f"(<code>.../workflow/xxxx</code> no sirve).</li>"
        f"{extra}"
        f"<li>Que el workflow importado sea <code>workflow_ciri_agent.json</code>.</li></ul>"
        f"<p>No se ejecuto el pipeline directo en su lugar: un informe asi pareceria "
        f"venir de la orquestacion sin haber pasado por ella.</p>",
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
            "<p>Con otra clave con saldo, el caso se investiga normalmente. Y con el "
            "<b>modo demo</b> encendido, los casos de ejemplo se ven igual, sin gastar.</p>",
            txn_id,
        )
    if _es_clave_invalida(exc):
        return _pagina(
            "Esa API key no es valida",
            "<p>Anthropic rechazo la clave. Suele ser que quedo cortada al pegarla, o que "
            "es de otro proveedor: las de Anthropic empiezan con <code>sk-ant-</code>.</p>"
            "<p>Se saca en <a href=\"https://console.anthropic.com/settings/keys\" "
            "target=\"_blank\">console.anthropic.com</a>. Sin clave, el <b>modo demo</b> "
            "muestra los casos de ejemplo igual.</p>",
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
        # Sin saldo, el informe prearmado es mejor que una pagina de error: se
        # entrega el trabajo igual, con su cartel. Es lo que el panel advierte
        # antes de correr.
        if _es_falta_de_saldo(exc):
            demo = _respuesta_demo(
            req.transaction_id, settings, pipeline.db, por_falta_de_saldo=True
        )
            if demo is not None:
                return demo
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
    if _en_modo_demo(req, settings):
        yield from _emitir_demo(req, settings, pipeline)
        return
    try:
        for step, data in pipeline.run_streaming(req, model_name=settings.llm_model):
            yield _sse(step, data)
    except Exception as exc:
        logger.error("Pipeline SSE fallo para %s: %s", req.transaction_id, exc, exc_info=True)
        if _es_clave_invalida(exc):
            yield _sse("error", {"message": (
                "Anthropic rechazo esa API key. Revisá que este completa y que empiece "
                "con 'sk-ant-'. Con el modo demo encendido, los casos de ejemplo se ven "
                "igual sin necesidad de clave."
            )})
            return
        if _es_falta_de_saldo(exc):
            html = _html_demo(req.transaction_id, settings, getattr(pipeline, "db", None))
            if html is not None:
                logger.warning(
                    "SIN SALDO: %s se responde con su informe prearmado. La API key usada "
                    "no tiene credito.", req.transaction_id,
                )
                yield _sse("done", {"html": html, "usage": USO_DEMO})
                return
            yield _sse("error", {"message": (
                "La API key usada se quedo sin saldo, y este caso no tiene informe "
                "prearmado. Con una clave con credito se investiga normalmente."
            )})
            return
        yield _sse("error", {"message": "El analisis se interrumpio. Revisar los logs del servidor."})


def _emitir_demo(req: AnalyzeRequest, settings: Settings, pipeline=None):
    """El caso prearmado, anunciado como tal desde el primer evento."""
    yield _sse("start", {"transaction_id": req.transaction_id, "demo": True})
    html = _html_demo(req.transaction_id, settings, getattr(pipeline, "db", None))
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
    """Serve the interactive test panel page.

    Los umbrales del score antifraude se inyectan desde `domain/constants.py`:
    escritos a mano en el JavaScript ya se habian desincronizado —el panel los
    trataba como una escala 0-10 donde alto es malo, cuando el score es 0-100 y
    alto significa seguro—, y pintaba de rojo las transacciones mas confiables.
    """
    tmpl = report_gen.env.get_template("test_panel.html")
    return HTMLResponse(
        content=tmpl.render(
            score_severo=RISK_FRAUD_SEVERE,
            score_riesgoso=FRAUD_SCORE_HIGH_RISK_THRESHOLD,
        ),
        status_code=200,
    )


@router.get("/api/panel/server-key-status")
def server_key_status(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Check if the server has its own Anthropic API key configured.

    When True, BYOK is optional — the panel can run analyses using the server key.
    When False, visitors must provide their own key (BYOK required).
    """
    return JSONResponse({"has_server_key": bool(settings.anthropic_api_key)})


@router.get("/api/panel/demo-status")
def demo_status(settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Que casos se pueden ver sin gastar, y como arranca el toggle del panel.

    Los casos salen de la carpeta de informes, no de una lista escrita a mano:
    agregar un informe alcanza para que el panel lo ofrezca.
    """
    return JSONResponse({
        "demo_mode": settings.demo_mode,
        "casos": casos_demo(settings.demo_reports_path),
    })


# ─── Panel analyze endpoint ───────────────────────────────────────────────────


@router.get("/api/panel/n8n-status")
async def n8n_status(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Quick liveness check for n8n — used by the panel UI to show a status badge.

    Without CB_N8N_BASE_URL there is no instance to point at: the panel asks the
    user for one instead of advertising a URL that leads nowhere.
    """
    # Cuanto hace que una orquestacion de n8n toco esta API. Es la confirmacion
    # de que el workflow importado llega: no dice de donde vino, solo que paso.
    registro = getattr(request.app.state, "contacto_n8n", None)
    contacto = {
        "ultimo_contacto_hace_s": registro.hace_cuanto() if registro else None,
        "contactos": registro.total if registro else 0,
    }

    base = settings.n8n_base_url.rstrip("/")
    if not base:
        return {"configured": False, "available": False, **contacto}
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
        **contacto,
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
    if _en_modo_demo(req, settings):
        demo = _respuesta_demo(req.transaction_id, settings, pipeline.db)
        if demo is not None:
            return demo
        return HTMLResponse(
            content=_pagina_sin_caso_demo(req.transaction_id, settings), status_code=200
        )

    # Pidieron n8n explicitamente. Si no se puede, se dice: caer al pipeline
    # directo en silencio devolveria un informe que parece venir de la
    # orquestacion sin haber pasado por ella, que es peor que un error.
    if not direct:
        base = (n8n_base_url or settings.n8n_base_url or "").strip()
        if not base:
            return HTMLResponse(
                content=_pagina_sin_n8n(req.transaction_id), status_code=400
            )
        html = await _try_n8n(req, settings, report_gen, n8n_test, timeout_s, n8n_base_url)
        if html is None:
            return HTMLResponse(
                content=_pagina_n8n_no_respondio(req.transaction_id, base, n8n_test),
                status_code=502,
            )
        return HTMLResponse(content=html, status_code=200)

    # ── Modo produccion: el pipeline corre de verdad ──────────────────────
    # La clave que venga en la peticion reemplaza a la del servidor. Quien trae
    # la suya gasta de su cuenta, no de la de nadie mas.
    if req.api_key:
        with _byok_pipeline(req.api_key, pipeline, settings, request) as propio:
            return _correr_directo(propio, req, settings)
    if not settings.anthropic_api_key:
        return HTMLResponse(
            content=_pagina(
                "Hace falta una API key",
                "<p>El modo produccion ejecuta el analisis de verdad, y este servidor no "
                "tiene una clave propia configurada.</p>"
                "<p>Cargá la tuya en el campo <b>API key</b> del panel, o volvé al "
                "<b>modo demo</b> para ver los casos de ejemplo sin necesitar clave.</p>",
                req.transaction_id,
            ),
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
