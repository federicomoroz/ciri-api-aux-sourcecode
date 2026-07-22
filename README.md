# CIRI Chargeback Agent

Intelligent chargeback resolution agent built for the CIRI (Continuous Improvement & Risk Intelligence) technical evaluation. The system investigates chargeback cases end-to-end: it retrieves applicable policies via RAG, evaluates them against the transaction, synthesizes a resolution, and self-improves through a Judge-gated feedback loop.

---

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │          n8n AI Agent (~19 nodes)    │
                        │    Orchestrator: WHAT and WHEN       │
                        │  (HTTP Tool Calls  →  FastAPI tools) │
                        └──────────────┬──────────────────────┘
                                       │ REST
                        ┌──────────────▼──────────────────────┐
                        │         FastAPI  (port 8000)         │
                        │     Business Logic: HOW              │
                        │  /api/transactions  /api/policies    │
                        │  /api/analyze/resolve  /api/judge    │
                        │  /api/feedback  /api/reports         │
                        └──────┬──────────────┬───────────────┘
                               │              │
              ┌────────────────▼──┐    ┌──────▼──────────────┐
              │   Qdrant (6333)   │    │   SQLite (.db file)  │
              │  Semantic truth   │    │  Structured truth     │
              │  - policies       │    │  - transactions       │
              │  - hist. cases    │    │  - cases / logs       │
              │  - _sem_cache     │    │  - policies / feedback│
              └───────────────────┘    └─────────────────────┘
                               │
              ┌────────────────▼──────────────────────────────┐
              │   Claude (Haiku by default) via Anthropic API  │
              │   4 prompts: policy_eval, resolution,          │
              │   judge, log_analysis (all versioned v1_*)     │
              └───────────────────────────────────────────────┘
```

**Key principle:** n8n is a thin orchestration shell (WHAT/WHEN). All domain logic, guardrails, and RAG live in FastAPI (HOW). No business logic in n8n IF nodes.

---

## Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| Docker + Docker Compose | >= 24.x | Runs Qdrant, FastAPI, n8n |
| Python | 3.11+ | Only needed outside Docker |
| Anthropic API Key | — | Claude Haiku (default model) |
| Langfuse account | — | Optional; for observability |

---

## Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd quest_ML
cp .env.example .env
# Edit .env — set CB_ANTHROPIC_API_KEY at minimum
```

### 2. Start all services

```bash
docker-compose up -d
```

Services started:
- Qdrant vector DB → http://localhost:6333
- FastAPI tools API → http://localhost:8000
- n8n orchestrator → http://localhost:5678

### 3. Seed database and vector store

```bash
docker-compose exec api python -m app.seed_data
```

This loads 100 transactions, 60 historical cases, 17 policies, and 150 logs into SQLite, then indexes policies and cases into Qdrant.

### 4. Verify health

```bash
curl http://localhost:8000/health
# {"status":"ok","qdrant":"ok","sqlite":"ok","embedding_model":"paraphrase-multilingual-MiniLM-L12-v2"}
```

### 5. Run a demo analysis

```bash
# Resolve chargeback for transaction TXN-00051 (Cripto — expect REJECT)
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051"}' | jq .
```

### 6. Open n8n

Navigate to http://localhost:5678, import `n8n/chargeback_agent_flow.json`, and activate the workflow.

---

## API Reference

All endpoints are prefixed with `/api/`. Full interactive docs: http://localhost:8000/docs

### Core analysis

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/analyze/resolve` | Policy evaluation + resolution synthesis + guardrails |
| `POST` | `/api/analyze/judge` | LLM-as-Judge quality evaluation (5 criteria, 1–10 each) |

### Transactions

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/transactions/{id}` | Get transaction by ID |
| `GET` | `/api/transactions/{id}/logs` | Get all event logs for a transaction |
| `GET` | `/api/transactions/{id}/client-history` | Chargeback history for the client |

### Policies (CRUD + semantic search)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/policies/` | List all policies |
| `GET` | `/api/policies/search` | Semantic search over Qdrant |
| `GET` | `/api/policies/{code}` | Get policy by code |
| `POST` | `/api/policies/` | Create policy → auto-indexed in Qdrant |
| `PUT` | `/api/policies/{code}` | Update policy → auto-re-indexed in Qdrant |
| `DELETE` | `/api/policies/{code}` | Delete policy → removed from Qdrant |

### Cases and merchants

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/cases/similar` | Semantic search for similar historical cases |
| `GET` | `/api/merchants/{name}/risk` | Merchant risk profile |
| `GET` | `/api/sla/{id}` | SLA status for a transaction |

### Feedback and reports

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/feedback/` | Submit analyst feedback; triggers auto-indexing if judge_score >= 8.0 |
| `GET` | `/api/reports/{id}` | HTML resolution report (Jinja2) |

### Logs

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/logs/analyze` | LLM semantic analysis of event logs |

---

## Configuration

All settings are read from `.env` with the `CB_` prefix (powered by pydantic-settings).

```env
# Required
CB_ANTHROPIC_API_KEY=sk-ant-...

# LLM (optional — defaults shown)
CB_LLM_MODEL=claude-haiku-4-5-20251001
CB_LLM_TEMPERATURE=0.3
CB_LLM_MAX_TOKENS=4096

# Qdrant (optional)
CB_QDRANT_URL=http://localhost:6333
CB_QDRANT_POLICIES_COLLECTION=policies
CB_QDRANT_CASES_COLLECTION=historical_cases
CB_QDRANT_CACHE_COLLECTION=_semantic_cache

# Embeddings (optional)
CB_EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
CB_EMBEDDING_DIM=384

# SQLite (optional)
CB_SQLITE_PATH=data/chargeback.db
CB_DATA_FILE_PATH=data/Similación_dataset_contracargos_.xlsx

# Semantic cache (optional)
CB_SEMANTIC_CACHE_ENABLED=true
CB_SEMANTIC_CACHE_THRESHOLD=0.92

# Auto-improvement gate (optional)
CB_JUDGE_AUTO_INDEX_THRESHOLD=8.0

# Langfuse observability (optional)
CB_LANGFUSE_ENABLED=false
CB_LANGFUSE_PUBLIC_KEY=pk-lf-...
CB_LANGFUSE_SECRET_KEY=sk-lf-...
CB_LANGFUSE_HOST=https://cloud.langfuse.com
```

---

## Testing

```bash
# All tests
docker-compose exec api pytest tests/ -v

# Unit tests only (no external services)
docker-compose exec api pytest tests/unit/ -v

# Integration tests (requires running Qdrant + SQLite)
docker-compose exec api pytest tests/integration/ -v

# Single test file
docker-compose exec api pytest tests/unit/test_query_builder.py -v

# With coverage
docker-compose exec api pytest tests/ --cov=app --cov-report=term-missing
```

Test structure:
```
tests/
  conftest.py          # shared fixtures, in-memory SQLite, mock Qdrant
  unit/
    test_query_builder.py    # deterministic query enrichment
    test_guardrails.py       # APPROVE+BLOCKER correction, compensation cap
    test_policy_eval.py      # policy verdict parsing
  integration/
    test_resolve_pipeline.py # full resolve → judge → feedback cycle
    test_rag_update.py       # policy CRUD triggers Qdrant re-index
```

---

## Design Decisions

### 1. n8n as thin orchestrator, FastAPI as logic layer

n8n handles scheduling, webhook entry points, and tool-call sequencing. It does not contain any chargeback domain logic. Every business decision (policy evaluation, guardrails, risk assessment) lives in FastAPI endpoints. This makes the logic independently testable and deployable without touching n8n.

### 2. Policies are data, not code

The 17 policies are stored as Markdown documents in Qdrant and as rows in SQLite. An analyst can create, update, or delete a policy via the REST API and it takes effect immediately — Qdrant is re-indexed on every write. No code change, no redeploy, no restart required.

### 3. Deterministic QueryBuilder for RAG

The query sent to Qdrant is built by rule-based logic, not by an LLM. A Cripto payment always appends "criptomonedas no reversible blocker"; a fraud_score below 30 appends "alto riesgo"; a non-LATAM country appends "plazo extendido". This makes retrieval reproducible, free (no token cost), and debuggable.

---

## Project Structure

```
quest_ML/
  api/
    app/
      config.py          # pydantic-settings (CB_ prefix)
      main.py            # FastAPI app, CORS, router registration
      dependencies.py    # lifespan, DI providers
      domain/
        models.py        # Pydantic request/response models
        enums.py         # PaymentMethod, VerdictType, etc.
      rag/
        indexer.py       # QdrantIndexer (batch + single point)
        retriever.py     # QdrantRetriever + QueryBuilder
        updater.py       # RAGUpdater (hooks for CRUD + feedback)
      llm/
        client.py        # AnthropicClient wrapper
        prompts/
          v1_policy_eval.py
          v1_resolution.py
          v1_judge.py
          v1_log_analysis.py
      routes/            # One file per domain (analyze, policies, etc.)
      reports/           # Jinja2 HTML report templates
      analysis/          # Log analysis helpers
      observability/     # Langfuse tracer wrapper
      data/
        db.py            # SQLite access layer
      seed_data.py       # Initial data loader
  n8n/
    chargeback_agent_flow.json   # n8n workflow export
  tests/
  docs/
  docker-compose.yml
  .env.example
```
