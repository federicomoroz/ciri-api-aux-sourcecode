from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from .dependencies import lifespan
from .routes import (
    analyze,
    cases,
    clients,
    feedback,
    health,
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5678",   # n8n
        "http://localhost:3000",   # front local
        "http://localhost:8000",   # panel served from same origin
        "null",                    # file:// — test panel abierto como archivo estático
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect root to the test panel."""
    return RedirectResponse(url="/panel")


app.include_router(health.router)
app.include_router(transactions.router)
app.include_router(logs.router)
app.include_router(clients.router)
app.include_router(policies.router)
app.include_router(cases.router)
app.include_router(merchants.router)
app.include_router(sla.router)
app.include_router(analyze.router)
app.include_router(feedback.router)
app.include_router(reports.router)
app.include_router(panel.router)
