"""
Test panel routes.

GET  /panel                    — serves the interactive test panel HTML page
GET  /api/panel/n8n-status     — liveness check for n8n (used by panel UI badge)
POST /api/panel/analyze        — runs analysis via n8n (or direct pipeline fallback)
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from html import escape as html_escape

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
    FRAUD_SCORE_MODERADO,
    JUDGE_APPROVAL_THRESHOLD,
    JUDGE_NEEDS_REVIEW_THRESHOLD,
    LLM_TIMEOUT_S,
    N8N_HEALTHZ_PATH,
    N8N_PING_TIMEOUT_S,
    N8N_TIMEOUT_S,
    N8N_WEBHOOK_PATH,
    N8N_WEBHOOK_TEST_PATH,
    PASO_JUEZ,
    PASO_POLITICAS,
    PASO_RESOLUCION,
    SSE_LATIDO_S,
)
from ..domain.contratos import SumideroDeAlertas
from ..domain.fallos import RESPUESTAS, Fallo, clasificar
from ..domain.models import AnalyzeRequest
from ..reports.generator import ReportGenerator
from ..services.pipeline import PipelineService
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

LATIDO = ": latido {:.0f}s" + chr(10) * 2   # comentario SSE: el panel lo ignora

router = APIRouter(tags=["panel"])


def _sumidero_de_alertas(base: PipelineService) -> SumideroDeAlertas | None:
    """Donde anota las alertas un pipeline efimero: la misma base del proceso.

    Los eventos operativos —un rechazo automatico, un caso que necesita una
    persona— tienen que quedar registrados venga el caso por donde venga. Sin
    donde anotarlos se devuelve None, y eso es legitimo: pasa en los tests.

    Sigue siendo `getattr` porque `FuenteDeCasos` —lo que el pipeline declara
    necesitar de la base— no incluye anotar alertas, y no deberia: son dos
    capacidades distintas del mismo objeto. Lo que cambio es que ahora el tipo
    de retorno dice que se espera del otro lado.
    """
    return getattr(base.db, "save_alert", None)


@contextmanager
def _pipeline_con(base: PipelineService, request: Request, clientes: dict) -> Iterator[PipelineService]:
    """El pipeline de esta peticion, armado sobre los clientes que se le den.

    De donde salen esos clientes es lo unico que distingue a los dos modos de
    abajo, asi que el armado y el cierre viven aca una sola vez.

    Los clientes se cierran al salir: cada uno abre su propio pool de
    conexiones y sin esto cada peticion dejaba pools colgados hasta que pasara
    el recolector. El cierre se le pide al manager y no se hace a mano, porque
    el sabe cuales son suyos —compartidos, que reusa la proxima— y cuales son de
    esta corrida; cerrarlos todos dejaba inservibles a los compartidos.
    """
    modelos = request.app.state.modelos_service
    try:
        yield PipelineService(
            db=base.db, retriever=base.retriever, analyzer=base.analyzer,
            resolution_svc=ResolutionService(
                clientes[PASO_POLITICAS], request.app.state.tracer,
                llm_resolution=clientes[PASO_RESOLUCION],
                llm_judge=clientes[PASO_JUEZ],
                alertas=_sumidero_de_alertas(base),
            ),
            report_gen=base.report_gen,
        )
    finally:
        modelos.manager.cerrar_todos(clientes)


@contextmanager
def _pipeline_efimero(
    base: PipelineService, request: Request, api_key: str = "", override: dict | None = None,
) -> Iterator[PipelineService]:
    """Pipeline de una sola corrida, con la clave y/o los modelos de esta peticion.

    Dos cosas viajan por peticion y ninguna se guarda: la credencial del
    visitante y su eleccion de modelo. Asi quien evalua puede probar otro modelo
    —o su propia cuenta— sin cambiarle nada al resto.
    """
    modelos = request.app.state.modelos_service
    with _pipeline_con(base, request, modelos.clientes_para(override, api_key=api_key)) as p:
        yield p


@contextmanager
def _pipeline_demo(base: PipelineService, request: Request) -> Iterator[PipelineService]:
    """Pipeline con el modelo del modo demo y la clave del servidor.

    Es el que hace que evaluar el sistema no requiera cuenta propia.
    """
    modelos = request.app.state.modelos_service
    with _pipeline_con(base, request, modelos.clientes_demo() or {}) as p:
        yield p


def _es_falta_de_saldo(exc: Exception) -> bool:
    return clasificar(exc) is Fallo.SIN_SALDO


def _es_clave_invalida(exc: Exception) -> bool:
    """Pegar mal la clave es el error mas probable de quien trae la suya."""
    return clasificar(exc) is Fallo.CLAVE_INVALIDA


def _en_modo_demo(req: AnalyzeRequest, settings: Settings) -> bool:
    """Si esta peticion no tiene que gastar dinero.

    Manda el toggle del panel; si la peticion no dice nada, decide el servidor.
    Nota: aca no interviene la API key. Pedir modo demo teniendo clave es
    legitimo — es justamente como se mira un caso sin gastar.
    """
    return settings.demo_mode if req.demo_mode is None else req.demo_mode


def _modelo_del_demo(request: Request) -> dict | None:
    """El modelo con el que corre el modo demo, si hay uno configurado.

    Los prompts son deterministas y piden una estructura explicita, asi que
    cualquier modelo decente puede cumplirlos —lo verifica
    `tests/unit/test_compatibilidad_modelos.py`—. Con uno configurado, servir una
    grabacion en vez de correr es peor en todo: el informe envejece, no prueba
    nada, y correrlo no requiere que el visitante traiga cuenta.
    """
    modelos = getattr(request.app.state, "modelos_service", None)
    if modelos is None:
        return None
    try:
        return modelos.modelo_demo()
    except Exception:
        logger.warning("No se pudo resolver el modelo del modo demo", exc_info=True)
        return None


def _marcar_si_gratis(respuesta, request: Request, corre_gratis: bool):
    """Le pone el cartel del modelo al informe, si la corrida fue gratuita.

    El informe tiene que decir con que se corrio: no fue la configuracion
    documentada, y eso lo tiene que ver quien lo lee, no quien lee el codigo.
    """
    if not (corre_gratis and isinstance(respuesta, HTMLResponse) and respuesta.status_code == 200):
        return respuesta
    nueva = HTMLResponse(
        content=_marcar_corrida_gratis(respuesta.body.decode("utf-8"), request),
        status_code=200,
    )
    nueva.headers.update(
        {k: v for k, v in respuesta.headers.items() if k.lower().startswith("x-")}
    )
    nueva.headers["X-Modelo-Gratuito"] = "true"
    return nueva


def _marcar_corrida_gratis(html: str, request: Request) -> str:
    """Le pone al informe el cartel que dice con que modelo se corrio."""
    from ..data.precomputados import _con_cartel, cartel_modelo_gratis

    modelo = _modelo_del_demo(request) or {}
    return _con_cartel(html, cartel_modelo_gratis(modelo))


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


LOCALES = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def _url_local(base: str) -> bool:
    """Si esa URL apunta a la maquina de quien la escribio, no a la red."""
    sin_esquema = base.split("://", 1)[-1]
    return sin_esquema.split(":", 1)[0].split("/", 1)[0].lower() in LOCALES


def _pagina_n8n_no_respondio(
    txn_id: str, base: str, es_prueba: bool, base_del_servidor: str = "",
) -> str:
    """n8n estaba indicado pero no contesto. No se disimula con el pipeline directo."""
    ruta = N8N_WEBHOOK_TEST_PATH if es_prueba else N8N_WEBHOOK_PATH
    # La trampa mas comun, y la mas dificil de ver desde el browser: la llamada
    # la hace la API, no la pagina. Si la API corre en un contenedor, su
    # `localhost` es ella misma y n8n queda del otro lado — aunque en el browser
    # esa misma URL abra el editor sin problemas.
    aviso_local = ""
    if _url_local(base) and base_del_servidor and not _url_local(base_del_servidor):
        aviso_local = (
            "<p><b>Esa URL la llama la API, no tu navegador.</b> La API corre en otro lado, "
            f"asi que su <code>localhost</code> es ella misma y no n8n. Desde donde corre, "
            f"n8n esta en <code>{html_escape(base_del_servidor)}</code>: "
            "<b>vacia el campo de URL</b> del panel y se usa esa.</p>"
        )
    extra = (
        "<li>En modo <b>prueba</b> hay que apretar <b>Execute workflow</b> en el editor "
        "justo antes: el webhook de test escucha una sola ejecucion.</li>"
        if es_prueba else
        "<li>En modo <b>produccion</b> el workflow tiene que estar <b>activo</b>.</li>"
    )
    return _pagina(
        "Tu n8n no respondio",
        f"<p>Se llamo a <code>{html_escape(base)}{ruta}</code> y no hubo respuesta util.</p>"
        f"{aviso_local}"
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


def _modelo_que_corrio(req: AnalyzeRequest, settings: Settings, request=None) -> str:
    """El modelo con el que se resolvio, no el default de produccion.

    Se pasaba `settings.llm_model` siempre, asi que una corrida con el modelo
    del modo demo devolvia la misma respuesta HTTP diciendo dos cosas
    contradictorias: `x-modelo-gratuito: true` junto a
    `x-usage-json: {"model":"claude-haiku-...","cost_usd":0.0283}`. El informe
    nombraba bien a Gemini y la cabecera cobraba 2,8 centavos por una corrida
    que costo cero.

    Es el mismo error que ya se corrigio en la tabla de tarifas —un analisis con
    Gemini figuraba en $0.1050—: ahi se arreglo el precio, no el nombre del
    modelo que se le pasa para calcularlo.
    """
    if request is not None and _en_modo_demo(req, settings):
        demo = _modelo_del_demo(request)
        if demo:
            return demo.get("modelo") or settings.llm_model
    return settings.llm_model


def _correr_directo(
    pipeline: PipelineService, req: AnalyzeRequest, settings: Settings, request=None,
) -> HTMLResponse:
    """Ejecuta el pipeline directo y devuelve el informe, o una pagina de error."""
    try:
        html, usage = pipeline.run(req, model_name=_modelo_que_corrio(req, settings, request))
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


def _con_latido(eventos, cada_s: float = SSE_LATIDO_S):
    """Mantiene el stream hablando mientras un paso tarda.

    Los tres pasos del pipeline son llamadas al modelo y cada una puede tardar
    minutos: entre evento y evento el stream quedaba **mudo**. El cliente aborta
    a los N segundos y mostraba un mensaje que culpaba al cold start de Render
    con el servicio ya caliente; en 2 de 5 corridas medidas ni siquiera llegaba
    un evento de error, porque el generador seguia bloqueado en el modelo.

    Con un latido cada pocos segundos el stream nunca queda callado: quien mira
    ve que el analisis sigue vivo, y si algo revienta el error llega igual.

    El generador corre en un hilo porque es bloqueante; la cola es lo unico que
    los dos comparten.

    **Y hay que esperarlo al salir.** Si el navegador se desconecta, Starlette
    cierra este generador: el `with _pipeline_efimero(...)` de aguas arriba
    ejecuta su `finally` y cierra los clientes HTTP **mientras el hilo sigue
    adentro de una llamada al modelo con esos mismos clientes**. Resultado:
    tokens que se gastan despues de que el usuario cerro la pestania, y un
    «client has been closed» que muere en una cola que ya nadie lee — un
    `except: pass` a distancia — el hilo desacopla la emision del pipeline, asi
    que el `GeneratorExit` ya no llega adentro y corta ahi mismo.

    Por eso el `finally` avisa que se abandono y espera al hilo. La espera esta
    acotada por el timeout del propio modelo, que es lo unico que puede estar
    bloqueando: no se cuelga para siempre, y si aun asi no vuelve se sigue —
    dejar un hilo colgado es mejor que dejar la respuesta colgada.
    """
    import queue
    import threading

    cola: queue.Queue = queue.Queue()
    CORTE = object()
    abandonado = threading.Event()

    def bombear():
        try:
            for evento in eventos:
                cola.put(("evento", evento))
                if abandonado.is_set():
                    # Nadie del otro lado: no se empieza el paso siguiente.
                    break
        except Exception as exc:            # noqa: BLE001 — se reenvia al cliente
            # Se anota aca ademas de reenviarlo: esto corre en un hilo aparte y
            # lo que el cliente recibe es un mensaje para leer en pantalla. Sin
            # esta linea, un fallo del analisis por streaming no dejaba rastro
            # en el servidor y solo existia en el navegador de quien lo pidio.
            logger.exception("Fallo el analisis por streaming")
            cola.put(("error", exc))
        finally:
            cola.put(("fin", CORTE))

    hilo = threading.Thread(target=bombear, daemon=True)
    hilo.start()
    esperando = 0.0
    try:
        while True:
            try:
                tipo, carga = cola.get(timeout=cada_s)
            except queue.Empty:
                esperando += cada_s
                # Un comentario SSE: mantiene viva la conexion sin ensuciar los
                # eventos que el panel interpreta.
                yield LATIDO.format(esperando)
                continue
            if tipo == "evento":
                yield carga
            elif tipo == "error":
                raise carga
            else:
                return
    finally:
        abandonado.set()
        hilo.join(timeout=LLM_TIMEOUT_S)
        if hilo.is_alive():
            logger.warning(
                "El analisis siguio corriendo tras %.0fs de haberse cortado el stream",
                LLM_TIMEOUT_S,
            )


def _emitir(pipeline: PipelineService, req: AnalyzeRequest, settings: Settings, request=None):
    """Traduce los eventos del pipeline a lineas SSE.

    En modo demo hay dos caminos. Si el modelo configurado es de un free tier,
    **el pipeline corre de verdad**: no cuesta nada, y un analisis recien hecho
    prueba lo que una grabacion no puede. Si no, sale el caso prearmado con su
    cartel, que es el respaldo — no el plan.
    """
    gratis = (
        _en_modo_demo(req, settings)
        and request is not None
        and _modelo_del_demo(request) is not None
    )
    if _en_modo_demo(req, settings) and not gratis:
        yield from _emitir_demo(req, settings, pipeline)
        return
    try:
        for step, data in pipeline.run_streaming(
            req, model_name=_modelo_que_corrio(req, settings, request),
        ):
            if step == "done" and gratis and data.get("html"):
                data = {**data, "html": _marcar_corrida_gratis(data["html"], request)}
            yield _sse(step, data)
    except Exception as exc:
        logger.error("Pipeline SSE fallo para %s: %s", req.transaction_id, exc, exc_info=True)
        # El texto sale de `domain/fallos.py`, no se escribe aca: el mismo fallo
        # tiene que decir lo mismo por HTTP y por el streaming. Los handlers de
        # `main.py` no llegan a este camino —cuando el SSE ya emitio, la respuesta
        # salio con 200— asi que la clasificacion se comparte aunque el transporte
        # no pueda.
        if _es_clave_invalida(exc):
            yield _sse("error", {"message": RESPUESTAS[Fallo.CLAVE_INVALIDA].para_leer})
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
            # La causa sale del lugar comun; lo que se agrega es lo que solo
            # este camino sabe: que tampoco habia informe guardado al que caer.
            yield _sse("error", {"message": (
                RESPUESTAS[Fallo.SIN_SALDO].para_leer
                + " Este caso ademas no tiene informe prearmado, asi que no hay "
                "nada guardado que mostrar en su lugar."
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

    Son los mismos que usa el informe. El panel filtraba con RISK_FRAUD_SEVERE,
    que decide el nivel de riesgo y no como se pinta: el mismo score caia en una
    banda aca y en otra en el informe.
    """
    tmpl = report_gen.env.get_template("test_panel.html")
    return HTMLResponse(
        # Sin esto el navegador guarda el panel por heuristica y sigue corriendo
        # el JavaScript viejo despues de un deploy: los sintomas son de servidor
        # —«el formulario no aparece», «el boton pide algo que ya esta»— y la
        # causa es una copia en cache. El panel es una pagina de herramienta, no
        # un activo estatico: no hay nada que ganar cacheandola.
        headers={"Cache-Control": "no-store"},
        content=tmpl.render(
            umbral_juez_aprueba=JUDGE_APPROVAL_THRESHOLD,
            umbral_juez_revisa=JUDGE_NEEDS_REVIEW_THRESHOLD,
            umbral_score_alto=FRAUD_SCORE_HIGH_RISK_THRESHOLD,
            umbral_score_moderado=FRAUD_SCORE_MODERADO,
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
def demo_status(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Como arranca el toggle del panel y con que corre cada modo.

    Con un modelo de demo configurado, el modo demo corre el pipeline entero
    sobre **cualquier** transaccion del dataset: la misma base que produccion.
    Los informes guardados quedan solo de respaldo para cuando no hay modelo.
    """
    demo = _modelo_del_demo(request)
    produccion = {}
    modelos = getattr(request.app.state, "modelos_service", None)
    if modelos is not None:
        produccion = {
            paso: {"proveedor": cfg["proveedor"], "modelo": cfg["modelo"], "titulo": cfg["titulo"]}
            for paso, cfg in modelos.vigente().items()
        }
    return JSONResponse({
        "demo_mode": settings.demo_mode,
        "demo_modelo": demo,
        "demo_ejecuta": demo is not None,
        "produccion": produccion,
        # Solo importan cuando no hay modelo de demo: son el respaldo.
        "casos": casos_demo(settings.demo_reports_path),
    })


# ─── Panel analyze endpoint ───────────────────────────────────────────────────


@router.get("/api/panel/n8n-status")
async def n8n_status(
    request: Request,
    settings: Settings = Depends(get_settings),
    n8n_base_url: str = Query("", description="La que use el panel, si escribio una"),
) -> dict:
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

    # La del panel gana sobre la del servidor: es la que va a usar el analisis.
    # Chequear una y llamar a la otra daba un badge en verde seguido de «tu n8n
    # no respondio», que es peor que no tener badge.
    base = (n8n_base_url or settings.n8n_base_url).strip().rstrip("/")
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
    # Modo demo con un modelo gratuito: se corre de verdad. Cuesta lo mismo que
    # no correrlo y devuelve un analisis de ahora en vez de una grabacion.
    corre_gratis = _en_modo_demo(req, settings) and _modelo_del_demo(request) is not None

    # El modo demo es sobre PLATA, no sobre quien orquesta: pasar por n8n cuesta
    # lo mismo que no pasar, porque los nodos llaman a esta misma API y es ella
    # la que resuelve el modelo del demo. Por eso el modo demo NO puede devolver
    # antes de llegar al bloque de n8n: quien elige «n8n Production» tiene que
    # recibir un informe que paso por la orquestacion, no uno que se le parece.
    #
    # El default del selector es «Directo (sin n8n)», asi que pedir n8n es una
    # eleccion explicita — la unica que no se puede ignorar en silencio.
    pidieron_n8n = not direct

    # Modo demo sin free tier: el caso ya resuelto, sin pasar por el modelo ni n8n.
    if _en_modo_demo(req, settings) and not corre_gratis and not pidieron_n8n:
        demo = _respuesta_demo(req.transaction_id, settings, pipeline.db)
        if demo is not None:
            return demo
        return HTMLResponse(
            content=_pagina_sin_caso_demo(req.transaction_id, settings), status_code=200
        )

    if corre_gratis and not pidieron_n8n:
        fallo = ""
        try:
            with _pipeline_demo(pipeline, request) as demo:
                respuesta = _correr_directo(demo, req, settings, request)
            # `_correr_directo` no propaga: devuelve una pagina de error. Mirar
            # solo las excepciones dejaba pasar el 502 sin activar el respaldo.
            if respuesta.status_code == 200:
                return _marcar_si_gratis(respuesta, request, True)
            fallo = f"status {respuesta.status_code}"
        except Exception as exc:
            fallo = str(exc)
        logger.warning(
            "MODO DEMO: no se pudo correr %s (%s). Se responde con el informe guardado.",
            req.transaction_id, fallo,
        )
        respaldo = _respuesta_demo(req.transaction_id, settings, pipeline.db)
        if respaldo is not None:
            # «Cayo al informe guardado» y «el modelo esta bien configurado» se
            # ven igual desde afuera: 200 y un HTML. El motivo viaja en la
            # cabecera para poder distinguirlos sin leer los logs.
            respaldo.headers["X-Demo-Fallback"] = fallo[:180] or "desconocido"
            return respaldo
        return HTMLResponse(
            content=_pagina_sin_caso_demo(req.transaction_id, settings), status_code=200
        )

    # Efimero si el visitante trajo su clave o eligio otro modelo para esta
    # corrida: en los dos casos la configuracion es suya y no la del proceso.
    if req.api_key or req.modelos:
        with _pipeline_efimero(pipeline, request, api_key=req.api_key or "", override=req.modelos) as propio:
            respuesta = _correr_directo(propio, req, settings, request)
            return _marcar_si_gratis(respuesta, request, corre_gratis)

    # Pidieron n8n explicitamente. Si no se puede, se dice: caer al pipeline
    # directo en silencio devolveria un informe que parece venir de la
    # orquestacion sin haber pasado por ella, que es peor que un error.
    if not direct:
        base = (n8n_base_url or settings.n8n_base_url or "").strip()
        if not base:
            return HTMLResponse(
                content=_pagina_sin_n8n(req.transaction_id), status_code=400
            )
        html, formulario = await _try_n8n(
            req, settings, report_gen, n8n_test, timeout_s, n8n_base_url,
            # La base de ESTA peticion: el orquestador tiene que contestarle a la
            # misma API con la que el panel esta hablando.
            api_propia=(
                settings.api_url_para_n8n or str(request.base_url)
            ).rstrip("/"),
        )
        if html is None:
            return HTMLResponse(
                content=_pagina_n8n_no_respondio(
                    req.transaction_id, base, n8n_test, settings.n8n_base_url or "",
                ),
                status_code=502,
            )
        respuesta = HTMLResponse(content=html, status_code=200)
        if formulario:
            # El panel lee esto y abre la pestania. Va en una cabecera y no en
            # el cuerpo porque el cuerpo entra a un <iframe>, y desde adentro no
            # se puede sacar una ventana nueva.
            respuesta.headers["X-HITL-Form-Url"] = formulario
        return respuesta

    # ── Modo produccion: el pipeline corre de verdad ──────────────────────
    # La clave que venga en la peticion reemplaza a la del servidor. Quien trae
    # la suya gasta de su cuenta, no de la de nadie mas.
    # Modo demo con modelo configurado: corre de verdad, con la clave del
    # servidor y el modelo del demo — no con la configuracion de produccion.
    #
    # Si correr falla —el vector store caido, el free tier agotado— se cae al
    # informe guardado. Es el encadenamiento honesto: el modo demo intenta
    # ejecutar, y solo recita cuando no puede. Un 502 seria peor que un informe
    # viejo declarado como tal.
    if not (settings.anthropic_api_key or corre_gratis):
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
    return _marcar_si_gratis(
        _correr_directo(pipeline, req, settings, request), request, corre_gratis,
    )


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
    # El guard preguntaba solo por claves de Anthropic y cortaba antes de mirar
    # el modo demo, que once lineas mas abajo esta perfectamente manejado. En el
    # deploy —que tiene Gemini configurado y andando— apretar «Analizar» en la
    # configuracion por defecto devolvia «API key requerida»: el camino
    # principal del panel, roto, mientras `?direct=1` corria entero.
    #
    # La pregunta correcta no es «hay clave de Anthropic» sino «hay con que
    # correr», y el modo demo con free tier es una de las respuestas.
    if not (
        req.api_key
        or settings.anthropic_api_key
        or (_en_modo_demo(req, settings) and _modelo_del_demo(request) is not None)
    ):
        return StreamingResponse(
            iter([f"data: {json.dumps({'step': 'error', 'message': 'API key requerida. Ingresa tu Anthropic API Key.'})}\n\n"]),
            media_type="text/event-stream",
        )

    def generate():
        # El pipeline BYOK vive dentro del generador: la respuesta se consume
        # despues de que esta funcion retorna, asi que cerrarlo antes dejaria
        # al stream sin cliente con el que trabajar.
        try:
            if _en_modo_demo(req, settings) and _modelo_del_demo(request) is not None:
                with _pipeline_demo(pipeline, request) as demo:
                    yield from _con_latido(_emitir(demo, req, settings, request))
            elif req.api_key or req.modelos:
                with _pipeline_efimero(
                    pipeline, request, api_key=req.api_key or "", override=req.modelos,
                ) as propio:
                    yield from _con_latido(_emitir(propio, req, settings, request))
            else:
                yield from _con_latido(_emitir(pipeline, req, settings, request))
        except Exception as exc:
            logger.error("Streaming pipeline failed: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'step': 'error', 'message': 'Error interno del pipeline. Revisa que tu API key sea valida.'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _pagina_hacia_el_formulario(txn_id: str, destino: str) -> str:
    """Lleva al formulario de aprobacion en vez de contar que existe.

    El panel muestra los informes en un `<iframe>`, asi que navegarlo deja el
    formulario adentro del panel y con el origen correcto: el suyo arma la URL
    de envio desde `window.location`, o sea que incrustar su HTML lo mandaria al
    lugar equivocado. Navegar sirve; copiar no.

    Queda el link a la vista porque el iframe puede no llegar: si n8n corre en
    otra maquina que la del panel, la URL del formulario no es alcanzable desde
    el browser de quien mira.
    """
    seguro = html_escape(destino, quote=True)
    return (
        "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"UTF-8\">"
        f'<meta http-equiv="refresh" content="0;url={seguro}">'
        "<title>Aprobacion requerida</title></head><body style=\"margin:0;"
        "font-family:ui-sans-serif,system-ui,sans-serif;background:#f9fafb;color:#374151;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px\">"
        "<div style=\"text-align:center;max-width:460px\">"
        f"<p style=\"font-size:1.05rem;margin:0 0 .6rem\"><b>{html_escape(txn_id)}</b> necesita "
        "la decision de un analista.</p>"
        "<p style=\"margin:0 0 1rem;color:#6b7280;font-size:.9rem\">Abriendo el formulario de "
        "aprobacion&hellip;</p>"
        f'<a href="{seguro}" target="_blank" rel="noopener" style="color:#2563eb;font-size:.85rem">'
        "Si no se abre solo, entra por aca</a></div>"
        # Navega el `meta refresh` y no un `location.replace`: adentro de un
        # <script> las entidades HTML no se decodifican, asi que el `&` escapado
        # de la firma viajaria literal y el formulario rechazaria el link.
        "</body></html>"
    )


async def _try_n8n(
    req: AnalyzeRequest,
    settings: Settings,
    report_gen: ReportGenerator,
    n8n_test: bool,
    timeout_s: float = N8N_TIMEOUT_S,
    n8n_base_url_override: str | None = None,
    api_propia: str = "",
) -> tuple[str | None, str]:
    """(html, url del formulario HITL). El html es None si n8n no sirvio.

    n8n responde con datos crudos en JSON y el panel les aplica la plantilla.

    La segunda parte viene vacia salvo que el caso haya derivado a una persona:
    ahi n8n responde 303 con la URL del formulario en `Location`, y quien llama
    la necesita para abrirla en una pestania. Devolverla aparte y no solo
    incrustada en el html es lo que permite que el panel la abra: un `<iframe>`
    no puede sacar una ventana nueva desde adentro.
    """
    base = (n8n_base_url_override or settings.n8n_base_url).rstrip("/")
    if not base:
        logger.info("panel: sin instancia de n8n configurada, se usa el pipeline directo")
        return None, ""
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
                    # A que API tiene que contestarle el orquestador. Sin esto el
                    # workflow usa su default, que puede ser OTRA instalacion: el
                    # panel local disparaba n8n y las alertas, el feedback y el
                    # informe cacheado terminaban en el deploy publico, asi que el
                    # panel los buscaba donde no estaban. El caso quedaba partido
                    # en dos APIs.
                    "api_base_url": api_propia,
                },
            )
        # Un caso que necesita una persona no es un fallo de n8n: el workflow
        # redirige al formulario del Wait, que es donde se toma la decision.
        # Sin esta rama el panel lo leia como «n8n no respondio» y ofrecia
        # reintentar — justo cuando el sistema estaba haciendo lo correcto.
        if r.status_code in (301, 302, 303, 307, 308):
            destino = r.headers.get("location", "")
            if destino:
                logger.info(
                    "panel: %s quedo esperando una decision humana; se lleva al formulario %s",
                    req.transaction_id, destino,
                )
                return _pagina_hacia_el_formulario(req.transaction_id, destino), destino
            logger.warning("panel: n8n redirigio sin Location — se usa el pipeline directo")
            return None, ""

        if r.status_code != 200:
            logger.warning("panel: n8n returned status=%s — falling back to direct", r.status_code)
            return None, ""

        content_type = r.headers.get("content-type", "")

        # JSON response: raw data from n8n → apply HTML template locally
        if "application/json" in content_type:
            data = r.json()
            # Cache hit: n8n returns {cached: true, html: "..."} directly
            if data.get("cached") and data.get("html"):
                logger.info("panel: n8n cache hit for %s", req.transaction_id)
                return data["html"], ""
            # Normal: render HTML from raw data
            logger.info("panel: n8n returned raw data for %s — rendering HTML", req.transaction_id)
            return report_gen.render(data), ""

        # Legacy: text/html response (backwards compatible)
        if "text/html" in content_type:
            logger.info("panel: n8n returned HTML for %s", req.transaction_id)
            return r.text, ""

        logger.warning("panel: n8n unexpected content-type=%s — falling back to direct", content_type)
    except Exception as exc:
        logger.warning("panel: n8n unreachable at %s (%s) — falling back to direct", n8n_url, exc)
    return None, ""
