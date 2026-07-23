import logging
import uuid

import anthropic
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Structured JSON logging — parseable by log aggregators (ELK, Datadog, CloudWatch)
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
    datefmt="%Y-%m-%dT%H:%M:%S",
)

logger = logging.getLogger(__name__)

from .dependencies import lifespan
from .domain.constants import FALLBACK_REQUEST_ID
from .routes import (
    analyze,
    analytics,
    cache,
    cases,
    clients,
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

app = FastAPI(
    title="CIRI Chargeback Agent API",
    description=(
        "FastAPI tools for the n8n AI Agent to investigate chargeback cases. "
        "Each endpoint is a tool the AI Agent calls autonomously."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject X-Request-ID for log correlation across the request lifecycle."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5678",                          # n8n local
        "http://localhost:3000",                          # front local
        "http://localhost:8000",                          # panel local
        "https://ciri-chargeback-agent.onrender.com",    # Render production
        "https://federicomoroz.app.n8n.cloud",           # n8n Cloud
    ],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Usage-JSON"],
)

@app.exception_handler(anthropic.APIError)
async def anthropic_error_handler(request: Request, exc: anthropic.APIError):
    """Surface Anthropic API errors (credit exhaustion, rate limits) with actionable detail."""
    request_id = getattr(request.state, "request_id", FALLBACK_REQUEST_ID)
    logger.error("Anthropic API error [request_id=%s]: %s %s", request_id, type(exc).__name__, exc)
    status = exc.status_code if hasattr(exc, "status_code") else 502
    return JSONResponse(
        status_code=status,
        content={
            "error": f"LLM provider error: {type(exc).__name__}",
            "detail": str(exc),
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return structured JSON error with request_id instead of raw stack trace."""
    request_id = getattr(request.state, "request_id", FALLBACK_REQUEST_ID)
    logger.error("Unhandled error [request_id=%s]: %s", request_id, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "request_id": request_id},
    )


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect root to the test panel."""
    return RedirectResponse(url="/panel")


app.include_router(health.router)
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
