import hmac
import logging
import uuid

import anthropic
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .dependencies import lifespan
from .domain.constants import (
    FALLBACK_REQUEST_ID,
    N8N_ORIGIN_HEADER,
)
from .domain.fallos import RESPUESTAS, clasificar
from .routes import (
    alerts,
    analytics,
    analyze,
    cache,
    cases,
    clients,
    config,
    feedback,
    health,
    langfuse,
    logs,
    merchants,
    panel,
    policies,
    reports,
    sla,
    transactions,
)

# Structured JSON logging — parseable by log aggregators (ELK, Datadog, CloudWatch)
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CIRI Chargeback Agent API",
    description=(
        "FastAPI tools for the n8n AI Agent to investigate chargeback cases. "
        "Each endpoint is a tool the AI Agent calls autonomously."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


class ContactoN8nMiddleware(BaseHTTPMiddleware):
    """Anota que una orquestacion de n8n llego, para que el panel lo confirme.

    Se mira una cabecera que pone el workflow, no el User-Agent: n8n usa el de
    axios, que tambien manda cualquier script, y confirmar de mas seria peor que
    no confirmar nada.
    """

    async def dispatch(self, request: Request, call_next):
        if request.headers.get(N8N_ORIGIN_HEADER):
            registro = getattr(request.app.state, "contacto_n8n", None)
            if registro is not None:
                registro.registrar()
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject X-Request-ID for log correlation across the request lifecycle."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# Paths exempt from API key auth (public-facing)
_PUBLIC_PATHS = frozenset({"/", "/health", "/panel"})
_PUBLIC_PREFIXES = ("/api/panel/",)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header for all non-public routes.

    If admin_api_key is empty (dev mode), the middleware is a no-op.
    """

    async def dispatch(self, request: Request, call_next):
        settings = getattr(request.app.state, "settings", None)
        admin_key = getattr(settings, "admin_api_key", "") if settings else ""

        if not admin_key:
            return await call_next(request)

        path = request.url.path.rstrip("/") or "/"
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        # compare_digest: el tiempo de comparacion no revela cuantos caracteres
        # de la clave se acertaron.
        if not hmac.compare_digest(provided, admin_key):
            return JSONResponse(status_code=401, content={"error": "Invalid or missing API key"})

        return await call_next(request)


app.add_middleware(APIKeyMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(ContactoN8nMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5678",                          # n8n local
        "http://localhost:3000",                          # front local
        "http://localhost:8000",                          # panel local
        "https://ciri-chargeback-agent.onrender.com",    # Render production
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Usage-JSON"],
)

def _sin_clasificar(request: Request, exc: Exception) -> JSONResponse:
    """Lo que no tiene nombre: un 500 honesto con su request_id.

    No se inventa una causa. Si el proveedor del modelo fallo de una forma que el
    sistema no sabe nombrar, se dice eso y se conserva su codigo: un 429 suyo no
    es un error de este servicio.
    """
    request_id = getattr(request.state, "request_id", FALLBACK_REQUEST_ID)
    logger.error("Unhandled error [request_id=%s]: %s", request_id, exc, exc_info=True)
    if isinstance(exc, anthropic.APIError):
        return JSONResponse(
            status_code=getattr(exc, "status_code", 502) or 502,
            content={
                "error": f"El proveedor del modelo fallo: {type(exc).__name__}",
                "detail": "Revisar los logs del servidor por el detalle completo.",
                "request_id": request_id,
            },
        )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": request_id},
    )


@app.exception_handler(Exception)
async def manejar_error(request: Request, exc: Exception) -> JSONResponse:
    """Traduce lo que fallo a algo que quien lee entienda.

    La clasificacion y los textos viven en `domain/fallos.py`, no aca: el mismo
    fallo tiene que decir lo mismo por esta ruta, por el streaming del panel y en
    la pagina HTML de error. Antes cada camino clasificaba por su cuenta —los
    tres con `MARKER in str(exc)`— y escribia su propia redaccion.
    """
    fallo = clasificar(exc)
    if fallo is None:
        return _sin_clasificar(request, exc)

    request_id = getattr(request.state, "request_id", FALLBACK_REQUEST_ID)
    r = RESPUESTAS[fallo]
    registrar = logger.error if r.alerta else logger.warning
    registrar("%s [request_id=%s]: %s", fallo, request_id, exc)
    return JSONResponse(
        status_code=r.status,
        content={"error": r.titulo, "detail": r.detalle, "request_id": request_id},
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect root to the test panel."""
    return RedirectResponse(url="/panel")


app.include_router(health.router)
app.include_router(alerts.router)
app.include_router(cache.router)
app.include_router(transactions.router)
app.include_router(logs.router)
app.include_router(clients.router)
app.include_router(policies.router)
app.include_router(cases.router)
app.include_router(merchants.router)
app.include_router(sla.router)
app.include_router(analyze.router)
app.include_router(feedback.router)
app.include_router(analytics.router)
app.include_router(reports.router)
app.include_router(panel.router)
app.include_router(langfuse.router)
app.include_router(config.router)
