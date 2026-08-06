# Agente de Investigación de Contracargos

![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://img.shields.io/badge/tests-277%20passed-brightgreen)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![n8n](https://img.shields.io/badge/n8n-orchestrator-ff6d00)
![Claude](https://img.shields.io/badge/Claude-Haiku%20%2B%20Sonnet-blueviolet)
![Qdrant](https://img.shields.io/badge/Qdrant-vector%20DB-dc382c)
![Judge](https://img.shields.io/badge/Judge%20Score-9.1%2F10-gold)

Agente inteligente de resolución de contracargos. Investiga casos end-to-end: recupera políticas aplicables vía RAG, las evalúa contra la transacción, sintetiza una resolución con razonamiento analítico, y se auto-mejora a través de un feedback loop con LLM-as-Judge.

---

## El flujo de un vistazo

**[`docs/diagrams/workflow.html`](docs/diagrams/workflow.html)** — abrí ese archivo en cualquier navegador y tenés el workflow entero: los 29 pasos en orden de ejecución más las 3 salidas de error, agrupados por sección, con el endpoint que llama cada uno. Al tocar cualquier paso se abre una ficha con qué hace, de dónde recibe y hacia dónde sigue.

No necesita conexión, ni instalar nada, ni importar el workflow. Se adapta al tema claro u oscuro del sistema y se imprime a PDF desde el navegador.

---

## Para el evaluador — 3 formas de probar

### Opción A — Importar el workflow en tu n8n (cero configuración)

El entregable principal. Importá los 3 archivos de `n8n/` en cualquier instancia de n8n — Cloud, self-hosted o Desktop:

| Archivo | Descripción |
|---|---|
| `workflow_ciri_agent.json` | Workflow principal — 38 nodos (32 ejecutables + 6 sticky notes) |
| `workflow_ciri_errors.json` | Error handler (Error Trigger → POST /api/alerts/ → email opcional) |
| `workflow_ciri_form.json` | Form trigger (formulario nativo de n8n como entrada alternativa) |

**No hay que configurar nada**: los nodos apuntan por defecto a la API pública en Render. Sin variables, sin credenciales, sin API keys — toda la autenticación con Anthropic y Voyage AI la maneja el backend.

Activá el workflow y disparalo:

```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

Para apuntarlo a otra API (por ejemplo la tuya local), en orden de prioridad:

| Prioridad | Cómo | Cuándo usarlo |
|---|---|---|
| 1 | `api_base_url` en el body del webhook | Override por request, funciona en cualquier n8n |
| 2 | Settings → Variables → `API_BASE_URL` | n8n con licencia (Variables es feature paga) |
| 3 | Default `https://ciri-chargeback-agent.onrender.com` | Si no configurás nada |

> **Tip:** El nodo "Despertar API" hace un `GET /health` antes de las queries para absorber el cold start de Render automáticamente.

### Opción B — Panel web en Render (0 setup, 30 segundos)

1. Abrir **[ciri-chargeback-agent.onrender.com/panel](https://ciri-chargeback-agent.onrender.com/panel)**
2. Seleccionar un escenario (ej: TXN-00051 BLOCKER) y hacer click en **Analizar**
3. Ver el pipeline ejecutarse en tiempo real vía SSE streaming

> **Nota:** Render free tier tiene cold start de ~50s en la primera carga. Después responde en ~12s por caso.

El panel es un extra, no un entregable pedido: sirve para ver el pipeline paso a paso sin montar nada. Soporta 3 modos: **Directo** (default, SSE streaming), **n8n Test** y **n8n Producción** — si importaste el workflow, podés pegar tu URL de n8n y ejecutarlo desde ahí.

### Opción C — Docker Compose (todo local, ~5 min)

```bash
git clone https://github.com/federicomoroz/ciri-chargeback-agent.git
cd ciri-chargeback-agent
cp .env.example .env
# Editar .env → poner CB_ANTHROPIC_API_KEY y CB_VOYAGE_API_KEY
docker-compose up -d
# Abrir http://localhost:8000/panel
```

Levanta Qdrant + FastAPI + n8n. Todo se inicializa solo: SQLite se seedea desde el Excel y Qdrant se indexa en el primer arranque, sin paso de seed manual.

Para que el workflow use tu API local en vez de Render, mandá `api_base_url` en el body:

```bash
curl -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra", "api_base_url": "http://api:8000"}' \
  -o reporte.html
```

---

## Los 7 ejes de la consigna

Mapeo completo con evidencia y comandos de verificación en [`docs/ejes.md`](docs/ejes.md).

| Eje | Dónde está | Verificación rápida |
|---|---|---|
| **1. Ingesta** | Webhook + Form Trigger (n8n), API directa, Excel → SQLite | `POST /webhook/chargeback-agent` |
| **2. RAG** | 3 colecciones Qdrant, embeddings Voyage, QueryBuilder determinístico, sin chunking (y [por qué](docs/rag_explanation.md#estrategia-de-chunking)) | `GET /api/policies/search` devuelve la `query_used` |
| **3. Agente** | 7 tools HTTP determinísticas · memoria = precedentes reindexados + caché de informes · 3 prompts versionados | [`docs/prompts.md`](docs/prompts.md) |
| **4. Automatización** | Switch por `risk_level`, HITL con Wait, reportes Jinja2, workflow de alertas | `docs/examples/*.html` |
| **5. Identificación de fallas** | 8 patrones de error sobre logs, `cb_ratio` por comercio, flags de cliente | `GET /api/merchants/Airbnb/risk` |
| **6. Auto-mejora** | Feedback loop, 5 guardrails (3 detectan alucinación, 2 validan valores), reindexado del RAG, versionado de prompts | `PUT /api/policies/{code}` reindexa al instante |
| **7. Observabilidad** | Langfuse (tokens, costo, latencia, score del Judge), alertas, error handler | `GET /api/langfuse/stats` |

> **Sobre el eje 3:** el agente no usa el nodo AI Agent de n8n. El tool calling es determinístico
> —7 herramientas, siempre las mismas, siempre en el mismo orden— porque en una fintech regulada
> un auditor tiene que poder reconstruir qué se consultó en cada caso. El trade-off está
> argumentado en [`docs/decisions.md`](docs/decisions.md#1-orquestación-explícita-con-n8n-no-ai-agent).

---

## Arquitectura

```
n8n (orquestador explícito · Cloud o self-hosted)
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

### Stack

| Componente | Servicio | Notas |
|---|---|---|
| Orquestador | n8n (local vía Docker o Cloud) | 38 nodos, workflow exportado en `n8n/` |
| API + Services | FastAPI (Render o Docker) | Cold start ~50s en Render free tier |
| Vector DB | Qdrant (Cloud o local) | 3 colecciones: policies, cases, cache |
| Embeddings | Voyage AI | `voyage-multilingual-2`, 1024 dims, free tier |
| LLM | Anthropic (Haiku + Sonnet) | Haiku: policy eval · Sonnet: resolución + judge |
| Observabilidad | Langfuse | Opcional, se activa con `CB_LANGFUSE_ENABLED=true` |
| DB estructurada | SQLite | Auto-seedeado desde Excel en primer arranque |

---

## Prerequisitos (solo para setup local)

| Dependencia | Notas |
|---|---|
| Docker + Docker Compose | Para correr Qdrant + FastAPI + n8n localmente |
| [Anthropic API Key](https://console.anthropic.com/settings/keys) | Claude Haiku + Sonnet |
| [Voyage AI API Key](https://dash.voyageai.com/) | Free tier, registro en 1 minuto |

> **Langfuse** es opcional. Solo se activa si configurás `CB_LANGFUSE_ENABLED=true`.

---

## Inicio Rápido (Docker)

```bash
git clone https://github.com/federicomoroz/ciri-chargeback-agent.git
cd ciri-chargeback-agent
cp .env.example .env
# Editar .env → poner CB_ANTHROPIC_API_KEY y CB_VOYAGE_API_KEY
docker-compose up -d
```

**Eso es todo.** Al arrancar:
1. Qdrant se levanta → http://localhost:6333
2. FastAPI detecta que SQLite no existe → lo crea desde el Excel (100 TXN, 60 casos, 17 políticas, 150 logs)
3. FastAPI detecta que Qdrant está vacío → indexa políticas y casos automáticamente
4. n8n se levanta → http://localhost:5678 (importar workflows manualmente)
5. Panel listo → **http://localhost:8000/panel**

Verificar:

```bash
curl http://localhost:8000/health
# {"status":"healthy","sqlite":"ok","qdrant":"ok","collections":{"policies":17,"historical_cases":60}}
```

### Importar workflow de n8n (opcional)

1. Ir a http://localhost:5678 → Import → seleccionar los 3 archivos de `n8n/`
2. Activar los workflows

No hace falta configurar variables ni credenciales. Si querés que el workflow use tu API local en vez de la de Render, pasá `api_base_url` en el body:

```bash
# Probar vía n8n, contra la API local
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra", "api_base_url": "http://api:8000"}' \
  -o report_blocker.html
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

### Panel interactivo

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/panel` | Panel interactivo de testing (3 modos: directo, n8n test, n8n prod) |
| `POST` | `/api/panel/analyze` | Análisis via n8n webhook (o fallback directo). Acepta `?n8n_base_url=` para usar tu propia instancia |
| `POST` | `/api/panel/analyze-stream` | Pipeline completo via SSE streaming con progreso en tiempo real |
| `GET` | `/api/panel/n8n-status` | Liveness check de n8n (badge del panel) |
| `GET` | `/api/panel/server-key-status` | Indica si el servidor tiene API key propia (BYOK opcional vs requerido) |

### Observabilidad y alertas

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/api/langfuse/stats` | Estadísticas de Langfuse (traces, tokens, costos) |
| `GET` | `/api/analytics/dashboard` | Métricas agregadas (transacciones, casos, feedback) |
| `GET` | `/health` | Health check de servicios |
| `POST` | `/api/alerts/` | Registrar alerta operativa (desde n8n error handler o pipeline) |
| `GET` | `/api/alerts/` | Listar alertas recientes (panel log, default 50) |

---

## Configuración

Todas las settings se leen de `.env` con prefijo `CB_` (via pydantic-settings).

```env
# ── Requeridos ─────────────────────────────────────────────
CB_ANTHROPIC_API_KEY=sk-ant-...          # Claude Haiku + Sonnet
CB_VOYAGE_API_KEY=pa-...                 # Voyage AI (free tier)

# ── LLM (opcionales, defaults mostrados) ──────────────────
CB_LLM_MODEL=claude-haiku-4-5-20251001  # policy eval
CB_LLM_MODEL_RESOLUTION=claude-sonnet-4-6  # resolución + judge
CB_LLM_TEMPERATURE=0.3
CB_LLM_MAX_TOKENS=4096

# ── Seguridad (opcional) ──────────────────────────────────
CB_ADMIN_API_KEY=                        # Si vacío = dev mode (sin auth)
                                         # Si seteado = todos los /api/* requieren X-API-Key header
                                         # Endpoints públicos: /panel, /api/panel/*, /health

# ── Qdrant (opcionales) ──────────────────────────────────
CB_QDRANT_URL=http://localhost:6333      # docker-compose usa http://qdrant:6333

# ── Caché de informes (opcional) ──────────────────────────
CB_REPORT_CACHE_ENABLED=true              # idempotencia por (transacción, VIP)

# ── Observabilidad Langfuse (opcional) ────────────────────
CB_LANGFUSE_ENABLED=false                # Activar para ver trazas LLM
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

277 tests en 16 archivos (unit + integration + E2E):

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
  e2e/
    conftest.py                      # httpx.Client contra API real (Render)
    test_api_real.py                 # 33 tests contra la API desplegada (LLM real, Qdrant real)
```

### Tests E2E (sin mocks — API real)

```bash
# Contra el deploy en Render (requiere API corriendo)
CB_E2E_BASE_URL=https://ciri-chargeback-agent.onrender.com pytest tests/e2e/ -v
```

33 tests E2E que verifican invariantes de negocio contra la API real:

| Suite | Tests | Qué verifica |
|-------|-------|-------------|
| Health | 2 | Health check, colecciones Qdrant |
| Transactions | 4 | Listado, lookup por ID, 404, logs |
| Policies | 3 | Listado, búsqueda por código, RAG semántico |
| Cases | 1 | Casos similares (Qdrant) |
| Analysis | 3 | Riesgo de comercio, historial de cliente, SLA |
| Full Pipeline | 6 | Streaming SSE, REJECT/BLOCKER, score Judge, HTML |
| Resolve | 6 | Resolve con contexto completo (LLM real) |
| Judge | 4 | Judge con contexto real (score >= 7.0) |
| Report | 1 | Generación de reporte HTML |
| Alerts | 2 | POST + GET alertas |
| Panel | 1 | Panel sirve HTML con autor |

---

## Decisiones de Diseño

13 decisiones documentadas con Contexto, Razonamiento, Trade-offs y consideraciones de producción. Ver [`docs/decisions.md`](docs/decisions.md) para el análisis completo.

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
| 9 | Caché de idempotencia, no semántico | Repetir un caso no vuelve a pagar el LLM, sin arriesgar respuestas cruzadas |
| 10 | Modelo dual Haiku + Sonnet | 9.1/10 Judge score vs 8.2 con Haiku solo |
| 11 | Data Tables de n8n descartadas | Sin agregaciones ni joins: la lógica volvería al canvas |
| 12 | Deuda asumida: el panel de 3112 líneas | No es entregable; el riesgo de tocarlo supera al beneficio |
| 13 | Un solo tipo para el contexto del caso | Ocho parámetros que viajaban juntos eran un concepto disfrazado |

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
        constants.py        # 73+ umbrales y límites centralizados
      services/
        resolution.py       # ResolutionService: resolve + judge + guardrails
        feedback.py         # FeedbackService: feedback + auto-indexación
        pipeline.py         # PipelineService: orquestación para panel directo + SSE streaming
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
    workflow_ciri_agent.json  # Workflow principal (38 nodos: 32 exec + 6 sticky)
    workflow_ciri_errors.json # Error handler (Error Trigger → notificación)
    workflow_ciri_form.json   # Form trigger (formulario nativo n8n)
  scripts/
    seed_data.py              # Seeding Excel → SQLite + Qdrant
  tests/                      # 277 tests (unit + integration + E2E)
  docs/
    architecture.md           # Arquitectura del sistema, flujo n8n
    decisions.md              # 13 decisiones técnicas con razonamiento
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
| [`docs/ejes.md`](docs/ejes.md) | Los 7 ejes de la consigna, uno por uno, con evidencia y verificación |
| [`docs/architecture.md`](docs/architecture.md) | Arquitectura del sistema, flujo n8n, diagramas |
| [`docs/decisions.md`](docs/decisions.md) | 13 decisiones técnicas con razonamiento y trade-offs |
| [`docs/prompts.md`](docs/prompts.md) | Prompts documentados con versionado y evolución |
| [`docs/rag_explanation.md`](docs/rag_explanation.md) | Estrategia RAG, colecciones, QueryBuilder |
| [`docs/mejora_continua.md`](docs/mejora_continua.md) | Feedback loop, Judge, guardrails, auto-mejora |
| [`docs/demo_scenarios.md`](docs/demo_scenarios.md) | 3 escenarios demo con comandos curl |

---

## Autor

**Federico Palatnik Moroz**

Construido con **Claude Opus 4.6** (Anthropic) como asistente de desarrollo.
