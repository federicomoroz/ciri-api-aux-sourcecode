# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Agente Inteligente de Contracargos** — Technical test for CIRI (Continuous Improvement & Risk Intelligence). An AI agent that investigates chargeback cases for a fintech, combining RAG over semantic documents with structured SQL lookups.

## Commands

```bash
# Start all services
docker-compose up -d

# Verify health
curl http://localhost:8000/health

# Seed data (Excel → SQLite + index Qdrant) — must run before first use
python scripts/seed_data.py

# Run all tests (from project root)
pytest tests/ -v --tb=short

# Run a single test file
pytest tests/unit/test_data_loader.py -v

# n8n UI (import workflow after docker-compose up)
# http://localhost:5678 → Import → n8n/workflow_ciri_agent.json

# Test panel (works without n8n — direct pipeline fallback)
# http://localhost:8000/panel

# Test via n8n webhook: BLOCKER auto-reject (crypto + fraud score 8)
curl -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o report_blocker.html

# Test via n8n webhook: HITL (high risk, VIP client)
curl -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00042", "motivo": "Fraude con tarjeta", "cliente_vip": true}' \
  -o report_hitl.html

# Edit a policy dynamically (re-indexed in Qdrant immediately, no redeploy)
curl -X PUT http://localhost:8000/api/policies/POL-FRD-001 \
  -H "Content-Type: application/json" \
  -d '{"description": "Score mínimo ahora es 40..."}'
```

## Architecture

**n8n Explicit Orchestrator + FastAPI Tools + Qdrant + SQLite**

```
n8n (54 nodes, explicit) = orchestrator — every step is a named, visible node
FastAPI                  = pure execution — thin HTTP endpoints, all business logic in services
Qdrant                   = semantic truth (policies + historical cases + semantic cache)
SQLite                   = structured truth (transactions, logs, cases — exact lookup)
```

**The central principle**: n8n is the explicit orchestrator — no AI Agent black box. Every HTTP call is a named node. All business logic lives in FastAPI services. n8n nodes never contain business logic.

**"Unir RAGs con base de datos"** (mandatory pattern from spec):
- SQLite → exact facts (TXN data, all its logs, client history)
- Qdrant RAG → context (which policies apply, what was done before in similar cases)
- Both combined in the AI Agent's context → justified recommendation with citations

### n8n Flow (54 nodes: 43 exec + 11 sticky)

```
SECCIÓN 1 — Entrada
[Webhook — Entrada] → [Validar Formato TXN] (Code)

SECCIÓN 2 — Recopilación de Contexto (7 HTTP GET/POST calls)
→ [Obtener Transacción]   GET  /api/transactions/{id}
→ [Obtener Logs]          GET  /api/logs/{tx_id}
→ [Buscar Políticas]      GET  /api/policies/search     (RAG Qdrant)
→ [Buscar Casos Similares]GET  /api/cases/similar        (RAG Qdrant)
→ [Riesgo del Comercio]   GET  /api/merchants/{name}/risk
→ [Historial del Cliente] GET  /api/clients/{id}/history
→ [Verificar SLA]         POST /api/sla/check

SECCIÓN 3 — Análisis con IA
→ [Compilar Contexto]       (Code) — merge all 7 results
→ [Sintetizar Resolución]   POST /api/analyze/resolve  (LLM → Resolution)
→ [Juez de Calidad]         POST /api/analyze/judge    (LLM-as-Judge, score 1–10)

SECCIÓN 4 — Enrutamiento por riesgo
→ [Preparar Informe]        (Code) — build ReportRequest payload
→ [Switch — Nivel de Riesgo]
    BLOCKER → [Generar Reporte] POST /api/reports/html → [Responder] text/html
    HIGH    → [Generar Reporte] POST /api/reports/html → [Responder] text/html
    MEDIUM  → [Generar Reporte] POST /api/reports/html → [Responder] text/html
    LOW     → [Generar Reporte] POST /api/reports/html → [Responder] text/html
```

Each "Generar Reporte" node is identical — same endpoint, same payload shape (`ReportRequest`). The risk level difference is visible in the generated HTML report's content, not in the n8n routing logic.

### FastAPI routes (= agent tools)

| Route | Purpose |
|-------|---------|
| `GET /api/transactions/{id}` | Exact lookup by TXN-XXXXX |
| `GET /api/logs/{tx_id}` | All logs for a transaction |
| `GET /api/policies/search` | Qdrant semantic search |
| `GET /api/policies` + CRUD | Dynamic policy management |
| `GET /api/cases/similar` | Qdrant semantic precedents |
| `GET /api/merchants/{name}/risk` | CB ratio, flags |
| `POST /api/sla/check` | LATAM: 10d / non-LATAM: 15d |
| `POST /api/analyze/resolve` | LLM synthesis → Resolution |
| `POST /api/analyze/judge` | LLM-as-Judge (score 1-10) |
| `POST /api/feedback` | Auto-improvement loop |
| `POST /api/reports/html` | Jinja2 → HTML report |
| `GET /api/cache/lookup` | Idempotency cache (SQLite exact-match) |
| `GET /panel` | Interactive test panel (n8n or fallback) |
| `GET /health` | Service health check |

### Qdrant collections

| Collection | Docs | Notes |
|------------|------|-------|
| `policies` | 17+ (dynamic) | Markdown docs, editable via API, auto-reindexed |
| `historical_cases` | 60+ (auto-grows) | New cases indexed when Judge score ≥ 8.0 |
| `_semantic_cache` | N | Reduces LLM cost for similar queries (threshold 0.92) |

**What is NOT indexed in Qdrant**: Transactions (exact ID lookup only) and Logs (retrieved complete by transaction_id — no useful similarity).

Embeddings: `voyage-multilingual-2` (1024 dims, Voyage AI API, multilingual).

### Dataset quirks

The Excel file (`data/Similación_dataset_contracargos_.xlsx`) has 4 sheets:
- Row 1 is a decorative title — **skip it**
- Row 2 is headers
- Data starts at Row 3
- Sheet names contain emojis — use `openpyxl` with actual emoji names

### Key design decisions

1. **Policies are DATA, not CODE** — stored as Markdown in Qdrant. `PUT /api/policies/{code}` re-indexes immediately. No deploy needed.
2. **LLM evaluates policies**, not deterministic Python — because if policies are editable, their evaluation must be too.
3. **Query builder is deterministic** — no LLM used to build Qdrant queries:
   ```python
   # Cases: f"Contracargo en {merchant} por USD {amount:.2f}, {payment_method}, {country}, score {score}"
   # Policies: f"contracargo {motivo}, {channel}, {payment_method}, score {score}/100, {country}"
   ```
4. **Guardrail**: APPROVE with any BLOCKER verdict active → flag as likely hallucination.
5. **Feedback loop**: Judge score ≥ 8.0 → auto-index case as new precedent in Qdrant.

### Configuration (all vars prefixed `CB_`)

```python
# .env / environment variables
CB_ANTHROPIC_API_KEY=...
CB_LLM_MODEL=claude-haiku-4-5-20251001   # haiku for dev, sonnet for prod
CB_LLM_TEMPERATURE=0.3
CB_QDRANT_URL=http://localhost:6333
CB_SQLITE_PATH=data/chargeback.db
CB_DATA_FILE_PATH=data/Similación_dataset_contracargos_.xlsx
CB_LANGFUSE_ENABLED=false                # set true for observability
CB_SEMANTIC_CACHE_ENABLED=true
CB_JUDGE_AUTO_INDEX_THRESHOLD=8.0
```

## File structure

```
quest_ML/
├── docker-compose.yml
├── .env.example
├── n8n/
│   └── workflow_ciri_agent.json      # DELIVERABLE: 54 nodes (43 exec + 11 sticky)
├── scripts/
│   └── seed_data.py                  # Excel → SQLite + Qdrant
├── api/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── app/
│       ├── main.py                   # FastAPI app + startup indexing
│       ├── config.py                 # pydantic-settings (CB_ prefix)
│       ├── dependencies.py           # DI via FastAPI lifespan, all services in app.state
│       ├── domain/
│       │   ├── constants.py          # All business thresholds (SLA, RAG, LLM, guardrails)
│       │   ├── models.py             # Pydantic request models (ResolveRequest, ReportRequest, etc.)
│       │   └── enums.py              # StrEnums: Severity, RiskLevel, VerdictType, LogEventType...
│       ├── data/
│       │   ├── loader.py             # Excel → SQLite (handles row 1 skip + emoji sheets)
│       │   └── db.py                 # get_transaction, get_logs, get_client_history
│       ├── services/
│       │   ├── resolution.py         # ResolutionService: resolve + judge + guardrails
│       │   └── feedback.py           # FeedbackService: feedback + auto-indexing
│       ├── rag/
│       │   ├── indexer.py            # Policies + cases → Qdrant on startup
│       │   ├── retriever.py          # Query builder + Qdrant search
│       │   ├── formatter.py          # format_policies/cases_for_prompt
│       │   ├── embedder.py           # Voyage AI embedding wrapper
│       │   └── updater.py            # Re-index on policy edit or case resolution
│       ├── llm/
│       │   ├── client.py             # Protocol LLMClient + AnthropicClient impl
│       │   ├── parsing.py            # parse_json_safely (LLM output extraction)
│       │   └── prompts/
│       │       ├── v1_policy_eval.py   # Versioned prompt: policy compliance
│       │       ├── v1_resolution.py    # Versioned prompt: synthesis → Resolution JSON
│       │       └── v1_judge.py         # Versioned prompt: LLM-as-Judge (5 criteria)
│       ├── analysis/
│       │   └── analyzer.py           # merchant_risk, client_flags, detect_error_patterns, check_sla
│       ├── reports/
│       │   ├── generator.py          # Jinja2 → HTML
│       │   └── templates/
│       │       └── case_report.html  # 9 sections + conditional HITL form
│       ├── observability/
│       │   └── tracer.py             # LangfuseTracer + NoOpTracer (for tests)
│       └── routes/                   # One file per domain = one agent tool
├── tests/
│   ├── conftest.py                   # Fixtures: mock LLM, SQLite in-memory
│   ├── unit/
│   │   ├── test_data_loader.py
│   │   ├── test_rag_retriever.py
│   │   ├── test_analysis.py
│   │   ├── test_parsing.py
│   │   └── test_guardrails.py
│   └── integration/
│       ├── test_full_flow.py
│       └── test_policies_crud.py
└── docs/                             # All DELIVERABLES
    ├── architecture.md
    ├── prompts.md
    ├── rag_explanation.md
    ├── mejora_continua.md
    └── demo_scenarios.md
```

## Implementation order

Follow the 9 phases:
1. **Domain** — `enums.py` + `models.py` (everything depends on these)
2. **RAG pipeline** — `indexer.py`, `retriever.py`, `updater.py`
3. **LLM + analysis** — `client.py`, prompts, `analyzer.py`
4. **FastAPI routes** (all tools)
5. **Observability + guardrails** — Langfuse tracer, semantic cache, hallucination checks
6. **HTML reports** — Jinja2 generator + template
7. **n8n + Docker** — workflow + compose
8. **Seed script + main** — `seed_data.py`, `main.py`, `dependencies.py`
9. **Documentation** — all 5 deliverable docs

## Demo scenarios (real TXN IDs from dataset)

- `TXN-00051`: Crypto + fraud score 8 → **BLOCKER** auto-reject (POL-EXC-003 + POL-FRD-001 FAIL)
- `TXN-00042`: Credit card + score 4 + VIP client → **HIGH** → HITL Wait node activated
- `TXN-00089`: Booking.com + score 8 + USA → **WARNING** (POL-EXC-004 non-LATAM extended SLA)
