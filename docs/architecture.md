# Architecture — CIRI Chargeback Agent

## Table of Contents

1. [System Overview](#system-overview) — Architecture Pattern
2. [n8n Explicit Orchestration](#n8n-explicit-orchestration)
3. [Full Flowchart](#full-flowchart)
4. [Modularity](#modularity)
5. [Scalability](#scalability)
6. [Data Flow Description](#data-flow-description)
7. [Architectural Decision Records](#architectural-decision-records)

---

## System Overview

### Architecture Pattern

**Explicit Workflow Orchestration with LLM-augmented tools** — sometimes called an *Agentic Pipeline*.

This is not an AI Agent. In a classic AI Agent, the LLM decides which tools to call and in what order. Here, **n8n decides the flow explicitly** — 42 nodes (37 executable + 5 sticky), always the same sequence, fully auditable. The LLM only reasons about the data it receives; it never controls the execution path.

| | AI Agent clásico | Este sistema |
|---|---|---|
| Quién decide el flujo | El LLM | n8n (explícito, 42 nodos) |
| Auditabilidad | Black box | Cada paso es un nodo visible |
| Determinismo | No garantizado | Siempre la misma secuencia |
| Debugging | Difícil | Nodo por nodo en el canvas |

The LLM's role is scoped and deliberate: it evaluates policy compliance, synthesizes a resolution with reasoning, and acts as a quality judge. It does not orchestrate.

---

The CIRI Chargeback Agent is a multi-service system where each layer has a **single, clearly bounded responsibility**:

| Layer | Technology | Responsibility |
|---|---|---|
| Orchestration | n8n (42 nodes: 37 exec + 5 sticky) | WHAT to do and WHEN — webhook, sequencing, native computation, guardrails visibility, routing by risk |
| Business logic | FastAPI | HOW — RAG retrieval, resolution synthesis with guardrails, feedback auto-indexing |
| Semantic store | Qdrant Cloud | Unstructured truth — policies, historical cases, semantic cache |
| Structured store | SQLite | Relational truth — transactions, logs, feedback, audit trail |
| LLM (resolve) | Claude via FastAPI | Policy evaluation + synthesis with guardrails and semantic cache |
| LLM (judge) | Claude via FastAPI | Quality scoring 1–10 — called from n8n via `POST /api/analyze/judge` |
| Observability | Langfuse | Token cost, latency, resolve scores, cache hit rate |

**Core principle:** n8n knows WHAT and WHEN; uses native nodes (Set, IF, Switch, Merge) for deterministic logic. FastAPI handles RAG, LLM synthesis with guardrails, feedback, and the Judge evaluation. All LLM calls are routed through FastAPI for consistent observability and prompt versioning.

---

## n8n Explicit Orchestration

The workflow contains **42 nodes (37 executable + 5 sticky notes) across 4 sections**. There is no AI Agent node, no black box, no tool calling decided by an LLM. Every step is a visible, named node with a specific purpose — native n8n nodes for deterministic logic, HTTP Request nodes for external calls.

```
§1 — ENTRY + CACHE (6 nodes)
   [Webhook — Entrada]              ← HTTP POST trigger (API/curl)
   [Validar Formato — IF]           ← IF node: validates TXN-XXXXX format
   [Validar Formato TXN]            ← Set node: normalizes fields
   [Despertar API]                  ← HTTP GET /health (wakes cold-start)
   [Verificar Caché]                ← HTTP GET /api/cache/lookup
   [¿Cache Hit?]                    ← IF node: cached → respond immediately

§2 — CONTEXT ASSEMBLY (11 nodes)
   [Obtener Transacción]         GET  /api/transactions/{id}
   [Obtener Logs]                GET  /api/logs/{tx_id}
   [Buscar Políticas]            GET  /api/policies/search     ← RAG: Qdrant semantic
   [Buscar Casos Similares]      GET  /api/cases/similar       ← RAG: Qdrant semantic
   [Riesgo del Comercio]         GET  /api/merchants/{name}/risk
   [Evaluar Riesgo Comercio]     ← Set node: is_suspended, is_high_risk, is_strategic
   [Historial del Cliente]       GET  /api/clients/{id}/history
   [Evaluar Historial Cliente]   ← Set node: is_recidivist, has_geo_anomaly, is_vip
   [Verificar SLA]               ← Set node: date math → within_sla, sla_limit_days
   [Merge — Contexto Paralelo]   ← Merge node: waits for 6 parallel branches

§3 — AI ANALYSIS (8 nodes)
   [Compilar Contexto]            ← Code node: merges all branch outputs
   [Sintetizar Resolución]        POST /api/analyze/resolve  ← LLM + RAG + guardrails
   [Verificar Guardrails]         ← Code node: defense-in-depth visibility
   [Juez de Calidad]              POST /api/analyze/judge    ← LLM-as-Judge
   [Extraer Evaluación — Juez]    ← Set node: JSON.parse → judge_evaluation
   [¿Juez Aprueba? (≥7.0)]       ← IF node: score ≥ 7.0 pass / < 7.0 fail
   [Marcar — Calidad Baja]        ← Set node: adds LOW_QUALITY flag
   [Preparar Informe]             ← Code node: builds ReportRequest payload

§4 — RISK ROUTING + RESPONSE (8 nodes unified)
   [Switch — Nivel de Riesgo]
      BLOCKER / MEDIUM / LOW → [Generar Reporte] → [Responder — Reporte]
      HIGH → [Notificar Analista] → [Wait — Aprobación HITL]
           → [Procesar Respuesta HITL] → [Registrar Feedback HITL]
           → [Generar Reporte — HITL] → [Responder — Reporte]
   All errors → [Stop and Error] → Error Handler workflow

§E — ERROR HANDLING (separate workflow: CIRI — Error Handler)
   [Error Trigger]               ← Captures errors propagated via Stop and Error
   [Extraer Info de Error]       ← Set node: error_message, failed_node, timestamp
```

**HITL Wait node** uses native n8n form submission — when an analyst visits the approval URL, they see a styled form with "Decisión" (APPROVE/REJECT dropdown) and "Notas del Analista" (textarea), not a raw JSON POST.

**Unified response path:** All branches converge to a single `[Responder — Reporte]` node. Cache hits go through `[Formatear Caché]` before reaching the same responder. Errors use `stopAndError` nodes that propagate to the Error Handler workflow — no `respondToWebhook` nodes on error paths.

**Why explicit instead of AI Agent?** An AI Agent node decides autonomously which tools to call and in what order. That creates a black box — no audit trail, non-deterministic sequencing, impossible to debug when it skips a step. The explicit workflow guarantees that every investigation always executes the same 7 context-gathering steps in the same order, every time.

---

## Full Flowchart

```mermaid
flowchart TD
    WEBHOOK([Webhook]) --> VALIDATE

    subgraph S1 [" "]
        VALIDATE{Validar formato}
        VALIDATE -->|valid| SET_TXN[Extraer campos]
        VALIDATE -->|invalid| ERR_TXN[Stop and Error]
    end

    SET_TXN --> WAKE[Despertar API] --> CACHE_CHECK[Verificar Caché]
    CACHE_CHECK --> CACHE_IF{Hit?}
    CACHE_IF -->|yes| FMT_CACHE[Formatear Caché] --> RESPOND
    CACHE_IF -->|no| GET_TX

    subgraph S2 [" "]
        GET_TX[Transacción]
        GET_TX -->|error| ERR_API[Stop and Error]
        GET_TX --> GET_LOGS[Logs] & SEARCH_POL[Políticas] & SEARCH_CASES[Casos similares] & MERCHANT[Comercio] & CLIENT[Cliente] & SLA[SLA]
        MERCHANT --> EVAL_M[Evaluar riesgo]
        CLIENT --> EVAL_C[Evaluar historial]
        GET_LOGS & SEARCH_POL & SEARCH_CASES & EVAL_M & EVAL_C & SLA --> MERGE[Merge]
    end

    subgraph S3 [" "]
        COMPILE[Compilar contexto]
        COMPILE --> RESOLVE[Resolución LLM]
        RESOLVE -->|error| ERR_LLM[Stop and Error]
        RESOLVE --> GUARDRAILS[Guardrails]
        GUARDRAILS --> JUDGE[Juez LLM]
        JUDGE -->|error| ERR_LLM
        JUDGE --> EXTRACT[Extraer score]
        EXTRACT --> JUDGE_IF{Score ≥ 7?}
        JUDGE_IF -->|yes| PREPARE[Preparar informe]
        JUDGE_IF -->|no| LOW_Q[Marcar baja calidad] --> PREPARE
    end

    subgraph S4 [" "]
        SWITCH{Nivel de riesgo}
        SWITCH -->|BLOCKER| REPORT[Generar Reporte]
        SWITCH -->|MEDIUM| REPORT
        SWITCH -->|LOW| REPORT
        SWITCH -->|HIGH| NOTIFY[Notificar analista]
        NOTIFY --> WAIT[Wait HITL]
        WAIT --> PROCESS[Procesar respuesta]
        PROCESS --> FEEDBACK[Registrar feedback]
        FEEDBACK --> REPORT_HITL[Generar Reporte HITL]
        REPORT -->|error| ERR_RPT[Stop and Error]
        REPORT_HITL -->|error| ERR_RPT
        REPORT --> RESPOND([Responder])
        REPORT_HITL --> RESPOND
    end

    MERGE --> COMPILE
    PREPARE --> SWITCH

    SEARCH_POL -.-> Q_POL[(policies)]
    SEARCH_CASES -.-> Q_CASES[(cases)]
    RESOLVE -.-> Q_CACHE[(_cache)]
    GET_TX -.-> DB_TX[(transactions)]
    GET_LOGS -.-> DB_LOGS[(logs)]

    style S1 fill:none,stroke:none
    style S2 fill:none,stroke:none
    style S3 fill:none,stroke:none
    style S4 fill:none,stroke:none
```

---

## Modularity

The system is structured in concentric layers. Each layer depends only on the layers below it. No layer has upward dependencies.

```
routes/          ← HTTP interface only. ~20 lines each. Zero business logic.
    ↓
services/        ← Orchestrates domain operations. No HTTP knowledge.
    ↓
analysis/ · rag/ · llm/   ← Pure domain logic. No FastAPI imports.
    ↓
data/            ← Pure data access. No business logic.
    ↓
domain/          ← Models, enums, constants. No external dependencies.
```

**Practical consequences of this structure:**

| Change needed | Files touched | Files untouched |
|---|---|---|
| Swap Anthropic → OpenAI | `llm/client.py` only | Everything else |
| Swap Qdrant → Pinecone | `rag/indexer.py` + `rag/retriever.py` | Everything else |
| Add new API endpoint | One file in `routes/` | All existing routes |
| Add new policy | `POST /api/policies/` (API call, no code) | Entire codebase |
| Update a prompt | One versioned file in `llm/prompts/` | Everything else |
| Change fraud score threshold | `domain/constants.py` line 1 | Everything else |

**n8n modularity:** Adding a new data source (e.g., a fraud score API) is one more HTTP Request node in §2. The rest of the workflow is untouched. Adding a new risk level is one more branch in the §4 Switch node.

**Protocol-based LLM client:** `llm/client.py` defines a `LLMClient` Protocol. `AnthropicClient` implements it. Tests use `MockLLMClient`. Swapping providers requires implementing the Protocol — no call sites change.

---

## Scalability

### Horizontal scaling (stateless API)

FastAPI is fully stateless. All state lives in Qdrant Cloud and SQLite. Multiple instances of the API can run behind a load balancer without coordination. Adding capacity is a one-line change in the deployment config.

### Knowledge base grows automatically

Every resolved case with `judge_score >= 8.0` is automatically indexed as a new precedent in Qdrant `historical_cases`. The RAG system improves over time without any manual intervention. A system that processed 1,000 chargebacks has 1,000+ precedents to draw from; a new installation has 60.

### Policies scale without code

The system supports any number of policies in any category. Adding a new regulatory requirement, a new payment method policy, or a new exception rule is a single API call. No code review, no deploy, no downtime. The LLM evaluates compliance from the natural language description.

### Semantic cache reduces LLM cost at scale

The `_semantic_cache` collection stores embeddings of recent resolutions. If an incoming request is semantically similar (cosine similarity ≥ 0.92) to a cached one, the LLM call is skipped entirely. In a production fintech processing thousands of similar cases daily, this dramatically reduces API cost.

### Versioned prompts enable safe iteration

All prompts are in versioned files (`v1_resolution.py`, `v1_judge.py`, etc.). Updating a prompt is a file change that can be A/B tested, rolled back, or deployed independently of the business logic. The version prefix makes it explicit which prompt version produced which resolution in the audit trail.

### Observable at every dimension

Langfuse traces every LLM call with: model, token count, latency, prompt version, judge score. This makes it possible to identify when a prompt version is underperforming, which merchants generate the most expensive cases, and what the p99 latency is per endpoint — without touching application code.

---

## Data Flow Description

### Phase 1: Entry and cache check

A chargeback investigation starts from a **Webhook** — `POST /webhook/chargeback-agent` with JSON body (`transaction_id`, `motivo`, `cliente_vip`).

`[Validar Formato — IF]` validates the `TXN-XXXXX` format. Invalid requests go directly to a `stopAndError` node. Valid requests pass through `[Despertar API]` (wakes cold-start services), then `[Verificar Caché]` checks the idempotency cache. On cache hit, `[Formatear Caché]` sends the stored HTML directly to `[Responder — Reporte]`, skipping the entire pipeline.

### Phase 2: Context assembly (§2 — 6 HTTP calls + native n8n nodes)

n8n fires 6 HTTP calls and uses 3 native Set nodes to gather all evidence:

1. `GET /api/transactions/{id}` — structured data from SQLite (amount, merchant, country, fraud_score, client_vip)
2. `GET /api/logs/{tx_id}` — all event logs for the transaction (INFO/WARN/ERROR severity)
3. `GET /api/policies/search` — semantic search over Qdrant `policies`; QueryBuilder enriches the query deterministically before embedding (see ADR-005)
4. `GET /api/cases/similar` — top-5 semantically similar historical cases from Qdrant
5. `GET /api/merchants/{name}/risk` — raw stats (cb_ratio, total_transactions, fraud_flags); **flags computed in n8n** via `[Evaluar Riesgo Comercio]` Set node (is_suspended, is_high_risk, is_strategic)
6. `GET /api/clients/{id}/history` — raw client history; **risk flags computed in n8n** via `[Evaluar Historial Cliente]` Set node (is_recidivist, has_geo_anomaly, is_vip)
7. `[Verificar SLA]` — **native n8n Set node** using date math expressions: `Math.floor((Date.now() - new Date(tx.date)) / 86400000)`, LATAM check inline, `sla_limit_days` (5 VIP / 10 LATAM / 15 non-LATAM)

All 6 parallel branches converge at `[Merge — Contexto Paralelo]` (Merge node, indices 0–5 explicitly connected).

### Phase 3: Resolution synthesis (§3)

`[Compilar Contexto]` merges all branch outputs — including both raw HTTP data and n8n-evaluated flags — into a single structured object. `POST /api/analyze/resolve` then executes internally:

1. Checks `_semantic_cache` — if hit (similarity ≥ 0.92), returns cached resolution immediately
2. Formats policies for LLM context via `rag/formatter.py`
3. Calls `v1_resolution` prompt → Resolution JSON with verdict, risk_level, reasoning, blockers
4. Applies post-LLM guardrails: APPROVE + BLOCKER active → force REJECT (hallucination guard)
5. Returns Resolution with any guardrail warnings appended

**`[Verificar Guardrails]`** — a native Code node that runs defense-in-depth checks directly on the n8n canvas, making guardrail status visible without opening FastAPI logs:
- APPROVE with BLOCKER → flagged
- Compensation > 110% of transaction amount → flagged
- Confidence > 0.95 with ≥2 policy failures → flagged

These are the same checks that FastAPI enforces — n8n provides canvas visibility, FastAPI provides enforcement.

`[Juez de Calidad]` calls `POST /api/analyze/judge` via FastAPI. The `v1_judge` prompt is version-controlled in `llm/prompts/v1_judge.py` and executed through the same `AnthropicClient` as all other LLM calls, ensuring consistent observability via Langfuse. The adjacent `[Extraer Evaluación — Juez]` Set node parses the FastAPI JSON response. Returns `overall_score` 1.0–10.0 across 5 criteria: factual accuracy, policy compliance, reasoning quality, risk classification, recommendation clarity.

**`[¿Juez Aprueba? (≥7.0)]`** — a native IF node that gates on the judge score. Scores ≥ 7.0 pass directly to `[Preparar Informe]`. Scores < 7.0 route through `[Marcar — Calidad Baja]`, a Set node that adds a `LOW_QUALITY` flag visible in the final report.

### Phase 4: Risk routing (§4)

`[Preparar Informe]` builds the `ReportRequest` payload. The Switch node routes by `resolution.risk_level`:

- **BLOCKER** — auto-reject. Crypto payment or fraud score ≤ 30 with active blocker policy. Report generated immediately.
- **HIGH** — elevated risk. VIP client or high-value transaction. Analyst notified → Wait HITL → feedback recorded → report generated.
- **MEDIUM** — standard risk. Report with full reasoning and recommended action.
- **LOW** — low risk. Expedited report with auto-approval recommendation.

BLOCKER, MEDIUM, and LOW converge to a single `[Generar Reporte]` node. HIGH uses `[Generar Reporte — HITL]` (references the HITL response). Both connect to the unified `[Responder — Reporte]` node. Errors on any report generation go to `[Stop and Error]` and propagate to the Error Handler workflow.

### Phase 5: Auto-improvement

When an analyst submits feedback via `POST /api/feedback`, `FeedbackService` saves it to SQLite. If `judge_score >= 8.0`, `RAGUpdater.on_case_resolved()` indexes the resolved case as a new precedent in Qdrant `historical_cases`. Future similar cases will retrieve this case as a high-quality example, continuously improving resolution quality.

---

## Architectural Decision Records

### ADR-001: n8n as Explicit Orchestrator (not AI Agent)

**Status:** Accepted

**Context:** The system needs an orchestration layer that provides a visual, auditable flow for non-technical stakeholders and guarantees deterministic execution order for every chargeback investigation.

**Decision:** Use n8n with 42 nodes (37 executable + 5 sticky notes) — no AI Agent node, no LLM-based tool calling in n8n. Every step is a visible node. Native n8n nodes (Set, IF, Switch, Merge, Wait) handle all deterministic logic. HTTP Request nodes are reserved for external calls. Both the synthesis LLM (`/api/analyze/resolve`) and the Judge (`/api/analyze/judge`) are called via FastAPI — all LLM interactions are centralized with consistent prompt versioning, error handling, and Langfuse observability. A unified `Responder — Reporte` node serves all response paths. Errors propagate to a separate Error Handler workflow via `stopAndError` nodes.

**Consequences:**
- Every investigation executes the exact same steps in the same order, every time
- The workflow is a complete visual audit trail — any stakeholder can open n8n and see exactly what happened
- Native n8n nodes handle SLA, merchant flags, client flags, and judge response parsing — zero FastAPI calls for deterministic logic
- Adding a new data source = one HTTP Request node + one Set node in §2, no code change
- The workflow JSON is version-controlled and importable in any n8n instance

**Alternatives rejected:** n8n AI Agent — non-deterministic tool call ordering, no audit trail, impossible to guarantee all 7 context sources are always consulted; LangGraph — adds Python dependency overhead, hides the visual flow.

---

### ADR-002: FastAPI for All Business Logic

**Status:** Accepted

**Context:** Business logic needs to be independently testable, versioned, and callable by multiple orchestrators (n8n today, potentially others tomorrow).

**Decision:** All domain logic lives in FastAPI behind clean HTTP endpoints. n8n communicates via REST only.

**Consequences:**
- Every piece of logic is testable with `pytest` independently of n8n
- 50 tests pass without any n8n or Qdrant running (mocked in `tests/conftest.py`)
- n8n is replaceable (Temporal, Airflow, a cron job) without touching FastAPI
- OpenAPI docs at `/docs` are auto-generated and always current

**Alternatives rejected:** Embedding logic in n8n Code nodes — not testable, not reusable, not independently versioned.

---

### ADR-003: Qdrant + SQLite Hybrid Storage

**Status:** Accepted

**Context:** Two fundamentally different data retrieval needs: semantic similarity (find policies/cases similar in meaning) and exact structured queries (get transaction by ID, filter logs by severity).

**Decision:** Qdrant for semantic data; SQLite for structured data. SQLite is write-primary; Qdrant is derived from it via `RAGUpdater`.

**Consequences:**
- Every policy CRUD operation triggers immediate Qdrant re-indexing — no stale embeddings
- SQLite provides a full audit trail with timestamps for every policy change
- No PostgreSQL dependency — SQLite runs in-process, zero configuration

**Alternatives rejected:** PostgreSQL with pgvector — operational overhead not justified; pure Qdrant — no structured query capability, no foreign keys, no audit trail.

---

### ADR-004: Policies as Data, Not Code

**Status:** Accepted

**Context:** Chargeback policies change frequently due to regulatory updates, network rule changes (Visa/Mastercard), and internal risk calibrations.

**Decision:** 17 policies stored as Markdown in Qdrant + rows in SQLite. REST API enables management. Every write re-indexes immediately.

**Example — adding a new fraud policy:**
```bash
POST /api/policies/
{"code": "POL-FRD-005", "category": "FRAUDE", "name": "Nuevo método de pago", "description": "..."}
```
Available to the next resolution request. No code change. No deploy. No downtime.

**Alternatives rejected:** Hard-coded Python classes — every policy change requires code review, PR, and deployment.

---

### ADR-005: Deterministic QueryBuilder for RAG

**Status:** Accepted

**Context:** Building Qdrant search queries requires domain enrichment. This could be done by an LLM (flexible, costly, non-deterministic) or by rule-based logic (reproducible, free, fast).

**Decision:** `QueryBuilder` in `rag/retriever.py` builds all queries without an LLM call:

| Condition | Enrichment |
|---|---|
| `payment_method == "Cripto"` | `"criptomonedas no reversible blocker"` |
| `fraud_score < 30` | `"transaccion de alto riesgo fraude score bajo"` |
| `country not in LATAM_COUNTRIES` | `"internacional fuera LATAM plazo extendido"` |
| `channel == "IVR"` | `"limite monto IVR"` |

**Consequences:**
- Same transaction always generates the same query — reproducible and debuggable
- Zero token cost at retrieval time
- For policies: `top_k=17, threshold=0.0` — retrieve all, let the LLM determine relevance
- For cases: `top_k=5, threshold=0.40` — only semantically meaningful precedents

**Alternatives rejected:** LLM-generated queries — adds latency and cost to every request, non-deterministic, harder to debug.

---

## Consideraciones de Seguridad

| Aspecto | Implementación |
|---------|---------------|
| API Keys | Variables de entorno con prefijo `CB_`, nunca en código fuente |
| CORS | Restringido a orígenes conocidos (`localhost:5678`, `:3000`, `:8000`) |
| Métodos HTTP | Solo `GET`, `POST`, `PUT`, `DELETE`, `OPTIONS` — sin wildcards |
| Headers | `Content-Type`, `Authorization`, `X-Request-ID` únicamente |
| XSS en reportes | Jinja2 con `autoescape=True` por defecto |
| SQL Injection | Queries parametrizadas (`?` placeholders) en todo `db.py` |
| PII en Qdrant | Solo datos de negocio indexados (merchant, monto, país). Sin nombres ni documentos personales |
| Prompt injection | LLM output validado contra Pydantic models (`validate_llm_output`); guardrails post-LLM detectan contradicciones |
| Request correlation | `X-Request-ID` en middleware para auditoría y trazabilidad |
| Error handling | Global exception handler retorna JSON estructurado, sin stack traces al cliente |
