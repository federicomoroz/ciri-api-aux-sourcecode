# CIRI Chargeback Agent

![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://img.shields.io/badge/tests-244%20passed-brightgreen)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![n8n](https://img.shields.io/badge/n8n-orchestrator-ff6d00)
![Claude](https://img.shields.io/badge/Claude-Haiku%20%2B%20Sonnet-blueviolet)
![Qdrant](https://img.shields.io/badge/Qdrant-vector%20DB-dc382c)
![Judge](https://img.shields.io/badge/Judge%20Score-9.1%2F10-gold)

Agente inteligente de resolución de contracargos construido para la evaluación técnica de CIRI (Continuous Improvement & Risk Intelligence). El sistema investiga casos de contracargo end-to-end: recupera políticas aplicables vía RAG, las evalúa contra la transacción, sintetiza una resolución con razonamiento analítico, y se auto-mejora a través de un feedback loop con Judge.

> **Demo en vivo:** [Panel de Testing en Render](https://ciri-chargeback-agent.onrender.com/panel) — interfaz interactiva para correr investigaciones sin setup local.
>
> **Nota:** Render free tier tiene cold starts de ~50 segundos. La primera carga puede demorar.

---

## Arquitectura

```
n8n Cloud (orquestador explícito)
    │
    ├── Webhook / Form Trigger
    │
    ├── 7 llamadas de contexto ──────────────────────► FastAPI (Render)
    │   ├── GET /api/transactions/{id}                     │
    │   ├── GET /api/logs/{tx_id}                          ├── Services
    │   ├── GET /api/policies/search (RAG)                 │   ├── ResolutionService
    │   ├── GET /api/cases/similar (RAG)                   │   ├── FeedbackService
    │   ├── GET /api/merchants/{name}/risk                 │   └── LangfuseStatsService
    │   ├── GET /api/clients/{id}/history                  │
    │   └── POST /api/sla/check                            ├── RAG
    │                                                      │   ├── Qdrant Cloud (3 colecciones)
    ├── POST /api/analyze/resolve                          │   └── Voyage AI (embeddings)
    │   ├── Call 1: Policy Eval (Haiku)                    │
    │   ├── Call 2: Synthesis (Sonnet)                     ├── LLM
    │   └── Guardrails + overrides determinísticos         │   └── Anthropic API
    │                                                      │
    ├── POST /api/analyze/judge                            ├── Análisis
    │   └── Call 3: Judge (Sonnet)                         │   └── SLA, risk flags, patterns
    │                                                      │
    ├── Switch por risk_level                              └── Storage
    │   └── POST /api/reports/html                             └── SQLite
    │
    └── Respuesta HTML
```

**Principio central:** n8n es el orquestador explícito — cada paso es un nodo nombrado y visible. Sin nodo AI Agent, sin black box. Toda la lógica de dominio, RAG y llamadas LLM viven en servicios de FastAPI.

**Principio de resolución:** "El código decide, el LLM explica" — 6 de 11 campos de la resolución son calculados determinísticamente por Python y siempre sobreescriben la salida del LLM.

### Stack y restricciones

| Componente | Servicio | Tier |
|---|---|---|
| Orquestador | n8n Cloud | Trial |
| API + Services | Render | Free (cold starts ~50s) |
| Vector DB | Qdrant Cloud | Free (1GB) |
| Embeddings | Voyage AI | Free |
| LLM | Anthropic (Haiku + Sonnet) | Pago por uso |
| Observabilidad | Langfuse Cloud | Free |
| DB estructurada | SQLite (efímero en Render) | — |

---

## Prerequisitos

| Dependencia | Versión | Notas |
|---|---|---|
| Docker + Docker Compose | >= 24.x | Para correr Qdrant, FastAPI, n8n localmente |
| Python | 3.11+ | Solo necesario fuera de Docker |
| Anthropic API Key | — | Claude Haiku + Sonnet |
| Voyage AI API Key | — | Free tier en https://dash.voyageai.com/ |
| Langfuse account | — | Opcional; para observabilidad |

---

## Inicio Rápido

### 1. Clonar y configurar

```bash
git clone <repo-url>
cd quest_ML
cp .env.example .env
# Editar .env — configurar CB_ANTHROPIC_API_KEY y CB_VOYAGE_API_KEY como mínimo
```

### 2. Iniciar servicios

```bash
docker-compose up -d
```

Servicios levantados:
- Qdrant → http://localhost:6333
- FastAPI → http://localhost:8000
- n8n → http://localhost:5678

### 3. Seed de datos

```bash
docker-compose exec api python -m app.seed_data
```

Carga 100 transacciones, 60 casos históricos, 17 políticas y 150 logs en SQLite, luego indexa políticas y casos en Qdrant.

### 4. Verificar health

```bash
curl http://localhost:8000/health
# {"status":"healthy","sqlite":"ok","qdrant":"ok","collections":{"policies":17,"historical_cases":60,"_semantic_cache":0}}
```

### 5. Importar workflow de n8n

Navegar a http://localhost:5678, importar `n8n/workflow_ciri_agent.json` (workflow principal, 54 nodos). Activar el workflow.

### 6. Correr un análisis demo

```bash
# Vía webhook de n8n
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o report_blocker.html

# Vía panel de testing de FastAPI (funciona sin n8n)
# http://localhost:8000/panel
```

---

## Referencia de API

Todos los endpoints bajo `/api/`. Docs interactivos: http://localhost:8000/docs

### Análisis principal

| Método | Endpoint | Descripción |
|---|---|---|
| `POST` | `/api/analyze/resolve` | Evaluación de políticas + síntesis de resolución + guardrails |
| `POST` | `/api/analyze/judge` | Evaluación de calidad LLM-as-Judge (5 criterios, 1–10) |

### Transacciones

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/transactions/{id}` | Transacción por ID |
| `GET` | `/api/logs/{tx_id}` | Logs de eventos de una transacción |
| `GET` | `/api/clients/{id}/history` | Historial de chargebacks del cliente |

### Políticas (CRUD + búsqueda semántica)

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/policies/` | Listar todas las políticas |
| `GET` | `/api/policies/search` | Búsqueda semántica en Qdrant |
| `GET` | `/api/policies/{code}` | Política por código |
| `POST` | `/api/policies/` | Crear política → auto-indexada en Qdrant |
| `PUT` | `/api/policies/{code}` | Actualizar política → re-indexada en Qdrant |
| `DELETE` | `/api/policies/{code}` | Eliminar política → removida de Qdrant |

### Casos, comercios y SLA

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/cases/similar` | Búsqueda semántica de casos similares |
| `GET` | `/api/merchants/{name}/risk` | Perfil de riesgo del comercio |
| `POST` | `/api/sla/check` | Verificación de cumplimiento SLA |

### Feedback, reportes y caché

| Método | Endpoint | Descripción |
|---|---|---|
| `POST` | `/api/feedback` | Feedback de analista; auto-indexa si judge_score >= 8.0 |
| `POST` | `/api/reports/html` | Generar reporte HTML (Jinja2) |
| `GET` | `/api/cache/lookup` | Verificación de caché de idempotencia (SQLite) |

### Observabilidad

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/langfuse/stats` | Estadísticas de Langfuse (traces, tokens, costos) |
| `GET` | `/health` | Health check de servicios |

---

## Configuración

Todas las settings se leen de `.env` con prefijo `CB_` (via pydantic-settings).

```env
# Requeridos
CB_ANTHROPIC_API_KEY=sk-ant-...
CB_VOYAGE_API_KEY=pa-...

# LLM (opcionales — defaults mostrados)
CB_LLM_MODEL=claude-haiku-4-5-20251001
CB_LLM_MODEL_RESOLUTION=claude-sonnet-4-6    # modelo más fuerte para síntesis + judge
CB_LLM_TEMPERATURE=0.3
CB_LLM_MAX_TOKENS=4096

# Qdrant (opcionales)
CB_QDRANT_URL=http://localhost:6333
CB_QDRANT_POLICIES_COLLECTION=policies
CB_QDRANT_CASES_COLLECTION=historical_cases
CB_QDRANT_CACHE_COLLECTION=_semantic_cache

# Embeddings (opcionales)
CB_EMBEDDING_MODEL=voyage-multilingual-2
CB_EMBEDDING_DIM=1024

# SQLite (opcionales)
CB_SQLITE_PATH=data/chargeback.db
CB_DATA_FILE_PATH=data/Similación_dataset_contracargos_.xlsx

# Caché semántico (opcionales)
CB_SEMANTIC_CACHE_ENABLED=true
CB_SEMANTIC_CACHE_THRESHOLD=0.92

# Gate de auto-mejora (opcional)
CB_JUDGE_AUTO_INDEX_THRESHOLD=8.0

# Observabilidad Langfuse (opcionales)
CB_LANGFUSE_ENABLED=false
CB_LANGFUSE_PUBLIC_KEY=pk-lf-...
CB_LANGFUSE_SECRET_KEY=sk-lf-...
CB_LANGFUSE_HOST=https://cloud.langfuse.com
```

---

## Tests

```bash
# Todos los tests (desde la raíz, fuera de Docker)
python -m pytest tests/ -v --tb=short

# Solo unit tests (sin servicios externos)
python -m pytest tests/unit/ -v

# Tests de integración
python -m pytest tests/integration/ -v
```

244 tests en 13 archivos (unit + integration):

```
tests/
  conftest.py                        # MockLLMClient, datos de ejemplo, SQLite in-memory
  unit/
    test_data_loader.py              # Carga Excel → SQLite
    test_rag_retriever.py            # Reglas de enriquecimiento del QueryBuilder
    test_analysis.py                 # SLA, patrones de error, riesgo de comercio, flags de cliente
    test_guardrails.py               # Validación post-LLM de guardrails
    test_guardrails_edge.py          # Edge cases: boundaries, warnings combinados
    test_db.py                       # Capa de base de datos: CRUD, stats, caché
    test_indexer.py                  # QdrantIndexer con client mockeado
    test_formatter.py                # Verificación de output del formatter RAG
    test_report_generator.py         # Rendering Jinja2 HTML + prevención XSS
    test_langfuse_stats.py           # Servicio de estadísticas Langfuse
  integration/
    test_full_flow.py                # Ciclo completo resolve → judge → feedback → report
    test_policies_crud.py            # CRUD de políticas + re-indexación en Qdrant
    test_routes.py                   # Integración a nivel de rutas: SLA, caché, health
```

---

## Decisiones de Diseño

10 decisiones documentadas con Contexto, Razonamiento, Trade-offs y consideraciones de producción. Ver [`docs/decisions.md`](docs/decisions.md) para el análisis completo.

| # | Decisión | Por qué |
|---|----------|---------|
| 1 | Orquestación explícita con n8n | Auditabilidad completa para fintech regulada |
| 2 | Políticas como datos, no código | Actualizaciones sin downtime vía API REST |
| 3 | QueryBuilder determinístico | Gratis, reproducible, debuggeable |
| 4 | Arquitectura de capas de servicio | Routes thin (~20 líneas), capas testeables |
| 5 | Embeddings Voyage AI (1024d) | Top-3 español multilingüe en MTEB, free tier |
| 6 | SQLite sobre Postgres | Self-contained para evaluación, migración limpia |
| 7 | Guardrails post-LLM + overrides | "El código decide, el LLM explica" |
| 8 | Judge a través de FastAPI | Versionado de prompts + observabilidad Langfuse |
| 9 | Caché semántico en Qdrant | ~20% reducción de costo LLM |
| 10 | Modelo dual Haiku + Sonnet | 9.1/10 Judge score vs 8.2 con Haiku solo |

---

## Escenarios Demo

Ver [`docs/demo_scenarios.md`](docs/demo_scenarios.md) para 3 escenarios end-to-end:

| TXN | Escenario | Resultado esperado |
|---|---|---|
| TXN-00051 | Cripto + fraud_score=8 | BLOCKER → auto-REJECT |
| TXN-00042 | Credit Visa + score=4 + VIP | HIGH → PENDING_HITL |
| TXN-00089 | Debit Visa + USA | WARNING (SLA extendido) |

---

## Estructura del Proyecto

```
quest_ML/
  api/
    app/
      config.py             # pydantic-settings (prefijo CB_)
      main.py               # App FastAPI, CORS, registro de routers
      dependencies.py       # DI via lifespan, todos los servicios inicializados una vez
      domain/
        models.py           # Modelos Pydantic con Field validators
        enums.py            # StrEnums: VerdictType, Severity, ErrorPattern, etc.
        constants.py        # 45+ umbrales y límites centralizados
      services/
        resolution.py       # ResolutionService: resolve + judge + guardrails
        feedback.py         # FeedbackService: feedback + auto-indexación
        pipeline.py         # PipelineService: orquestación para panel directo
        langfuse_stats.py   # Estadísticas de observabilidad
      rag/
        indexer.py          # QdrantIndexer (batch + single point, uuid5 IDs)
        retriever.py        # QdrantRetriever + QueryBuilder (determinístico)
        updater.py          # RAGUpdater (hooks para CRUD + feedback)
        formatter.py        # Formatters compartidos + matching de motivos
        embedder.py         # Voyage AI embedder (lazy, thread-safe)
      llm/
        client.py           # Protocol LLMClient + AnthropicClient
        parsing.py          # parse_json_safely (parsing de respuestas LLM)
        prompts/
          v1_policy_eval.py # v1.2 — evaluación de políticas
          v1_resolution.py  # v3.0 — síntesis de resolución (Sonnet)
          v1_judge.py       # v2.0 — LLM-as-Judge con rubrics
      analysis/
        analyzer.py         # SLA, patrones de error, riesgo, flags de cliente
      routes/               # Handlers thin (~20 líneas cada uno)
      reports/
        generator.py        # Jinja2 → HTML
        templates/
          case_report.html  # Reporte de caso (9 secciones + formulario HITL)
          test_panel.html   # Panel interactivo de testing
      observability/
        tracer.py           # LangfuseTracer + NoOpTracer (Protocol)
      data/
        db.py               # Acceso SQLite (datos puros, sin lógica de negocio)
        loader.py           # Excel → SQLite (maneja row 1 skip + hojas con emojis)
  n8n/
    workflow_ciri_agent.json  # Workflow principal (54 nodos: 43 exec + 11 sticky)
  scripts/
    seed_data.py              # Seeding Excel → SQLite + Qdrant
  tests/                      # 244 tests (unit + integration)
  docs/
    architecture.md           # Arquitectura del sistema, flujo n8n
    decisions.md              # 10 decisiones técnicas con razonamiento
    prompts.md                # Prompts documentados con versionado
    rag_explanation.md        # Estrategia RAG, colecciones, QueryBuilder
    mejora_continua.md        # Feedback loop, Judge, guardrails
    demo_scenarios.md         # 3 escenarios demo con comandos curl
  docker-compose.yml
  .env.example
```

---

## Documentación

| Documento | Descripción |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Arquitectura del sistema, flujo n8n, diagramas |
| [`docs/decisions.md`](docs/decisions.md) | 10 decisiones técnicas con razonamiento y trade-offs |
| [`docs/prompts.md`](docs/prompts.md) | Prompts documentados con versionado y evolución |
| [`docs/rag_explanation.md`](docs/rag_explanation.md) | Estrategia RAG, colecciones, QueryBuilder |
| [`docs/mejora_continua.md`](docs/mejora_continua.md) | Feedback loop, Judge, guardrails, auto-mejora |
| [`docs/demo_scenarios.md`](docs/demo_scenarios.md) | 3 escenarios demo con comandos curl |
