# Agente de Investigación de Contracargos

![Python](https://img.shields.io/badge/python-3.11+-blue)
![Tests](https://img.shields.io/badge/tests-430%20passed-brightgreen)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![n8n](https://img.shields.io/badge/n8n-orchestrator-ff6d00)
![Claude](https://img.shields.io/badge/Claude-Haiku%20%2B%20Sonnet-blueviolet)
![Qdrant](https://img.shields.io/badge/Qdrant-vector%20DB-dc382c)
![Judge](https://img.shields.io/badge/Judge%20Score-9.1%2F10-gold)

Ante un contracargo, el agente reúne todo lo que se sabe del caso —la transacción, sus logs, las políticas que aplican, qué se resolvió en casos parecidos, el riesgo del comercio y el historial del cliente—, propone una resolución justificada y se autoevalúa. Los casos de riesgo alto frenan y esperan a un analista.

---

## Cómo usarlo

Hay **cuatro formas de usar el sistema**, y todas hacen lo mismo por dentro. Cambia por dónde entra el caso.

| | Forma | Para qué sirve | Qué necesitás |
|---|---|---|---|
| **1** | [Ver el circuito](#1-ver-el-circuito-sin-ejecutar-nada) | Entender qué hace el sistema sin ejecutarlo | Un navegador |
| **2** | [El panel web](#2-el-panel-web) | Verlo funcionar en 30 segundos, paso a paso | Un navegador |
| **3** | [El workflow de n8n](#3-el-workflow-de-n8n) | Ver la orquestación real, que es el entregable | Una instancia de n8n |
| **4** | [La API directa](#4-la-api-directa) | Integrarlo con otro sistema, o probar una pieza suelta | `curl` |

Ninguna requiere instalar nada ni configurar claves. Si preferís correr todo en tu máquina, está en [Todo local con Docker](#todo-local-con-docker).

### Dos modos, con un toggle en el panel

Investigar un caso cuesta dinero real —dos modelos, varias llamadas—. Evaluar una entrega no debería consumir la cuenta de nadie, así que el panel arranca en **modo demo** y el toggle cambia al otro:

| | **Modo demo** (por defecto) | **Modo producción** |
|---|---|---|
| **Qué casos** | Los 3 de ejemplo | Cualquier transacción del dataset |
| **Llama al modelo** | **No.** No es que intente y falle: no gasta | Sí, el pipeline completo |
| **Hace falta clave** | No | Sí — la del panel, o la del servidor si tiene |
| **Qué devuelve** | El informe ya generado, al instante | El análisis recién hecho |

**Si cargás tu API key, se usa la tuya y no la del servidor.** Esa es la forma de ver el sistema trabajando de verdad sobre cualquier caso, gastando de tu cuenta.

**Un caso sin análisis guardado recibe el más cercano en riesgo.** Si pedís `TXN-00004` y el modelo no está disponible, se responde con el ejemplo cuyo score antifraude está más cerca, y el cartel nombra las dos transacciones: *"pediste TXN-00004, esto es TXN-00051"*. El informe es entero del caso prestado — nunca los datos de una transacción con la resolución de otra.

Un informe prearmado nunca se hace pasar por uno recién hecho. Se declara en cuatro lugares: el cartel **DEMO (Caso prearmado)** que abre el HTML, la cabecera `X-Modo-Demo`, el uso que informa `cost_usd: 0.0`, y un warning en el log del servidor.

Esto vale también para el workflow de n8n: corre entero en modo demo, con las siete consultas de contexto reales y el informe generado de verdad. Lo único pregrabado es lo que hubiera contestado el modelo. El porqué y los trade-offs, en [`docs/decisions.md`](docs/decisions.md), decisión 14.

Lo que no cuesta nada funciona igual en los dos modos: transacciones, logs, búsqueda semántica de políticas y precedentes, riesgo del comercio, SLA e informes. El default del servidor se cambia con `CB_DEMO_MODE=false`.

> **Sobre la primera llamada:** la API está en el free tier de Render, que duerme tras 15 minutos sin uso. La primera petición puede tardar ~50 segundos en despertarla; las siguientes responden en ~12. El workflow de n8n ya contempla esto con un nodo que la despierta antes de empezar.

---

### 1. Ver el circuito, sin ejecutar nada

Abrí **[`docs/diagrams/n8n_workflow_analysis.html`](docs/diagrams/n8n_workflow_analysis.html)** en cualquier navegador.

Es el workflow entero en una página: los 29 pasos en orden de ejecución más las 3 salidas de error, con el endpoint que llama cada uno. Al tocar un paso se abre una ficha con qué hace, de dónde recibe y hacia dónde sigue. No necesita conexión ni instalar nada, y se imprime a PDF.

### 2. El panel web

**[ciri-chargeback-agent.onrender.com/panel](https://ciri-chargeback-agent.onrender.com/panel)**

Elegís un caso del dataset, apretás **Analizar** y ves el pipeline ejecutarse en vivo: cada consulta que hace, cuántas políticas recuperó, qué resolvió y qué puntaje le puso el Juez. Termina con el informe HTML completo.

Es la forma más rápida de ver el sistema funcionando de punta a punta. No es un entregable de la consigna: es una herramienta para poder probarlo sin montar nada.

El panel puede ejecutar de dos maneras: **Directo**, que corre el pipeline dentro de la API, o **a través de tu n8n**. Si elegís n8n hay que pegar la URL de tu instancia —este servidor no puede adivinar dónde corre—, y **si no responde te lo dice en vez de correr el pipeline directo por lo bajo**: un informe idéntico al real haciéndose pasar por una ejecución de la orquestación sería peor que un error.

Y al revés: cuando tu n8n llama a esta API, el panel lo confirma —*"tu n8n llegó hasta esta API hace 40 segundos"*—. Es la única forma de saber, desde tu lado, que el workflow importado llega. No guarda de dónde vino: la API es pública y compartida.

Tres casos que muestran comportamientos distintos:

| Caso | Qué tiene de particular | Cómo termina |
|---|---|---|
| `TXN-00051` | Cripto, score antifraude 8 | **BLOCKER** — rechazo automático, la operación es irreversible |
| `TXN-00042` | Tarjeta, score 4, cliente VIP | **HIGH** — frena y espera a un analista |
| `TXN-00089` | Comercio fuera de LATAM | **MEDIUM** — SLA extendido a 15 días hábiles |

### 3. El workflow de n8n

El entregable principal. Importá los tres archivos de [`n8n/`](n8n/) en cualquier instancia de n8n —Cloud, self-hosted o Desktop— y disparalo:

```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

Devuelve el informe HTML listo. **No hay que configurar variables, credenciales ni API keys**: los nodos apuntan por defecto a la API pública, que es quien habla con Claude y con Qdrant.

| Archivo | Qué es |
|---|---|
| `workflow_ciri_agent.json` | El orquestador: 38 nodos, 32 ejecutables |
| `workflow_ciri_form.json` | Un formulario como segunda vía de entrada. Tiene su propio trigger y, al recibir un caso, **llama al webhook del orquestador**: por eso corre los 29 pasos igual |
| `workflow_ciri_errors.json` | Recibe los fallos de los otros dos y los registra |

Tres pasos manuales al importar, inevitables porque n8n reasigna identificadores al recibir un workflow:

1. **Activar los workflows.** n8n los importa desactivados siempre.
2. En el orquestador **y** en el del formulario: **Settings → Error Workflow → `workflow_ciri_errors`**. Sin eso, los fallos quedan sólo en la ejecución y no llegan al log de alertas.
3. **En el formulario, poner el Form Path.** Al importar desde la interfaz, n8n reemplaza el path del archivo por un identificador propio. Abrí el nodo **Form Trigger**, escribí `chargeback-form` en el campo **Form Path** y guardá. Ahí el formulario queda en `/form/chargeback-form`; si preferís el que generó n8n, la URL también está a la vista en ese mismo nodo.

Para apuntarlo a otra API, en orden de prioridad:

| | Cómo | Cuándo |
|---|---|---|
| 1 | `api_base_url` en el body del webhook | Override por request, funciona en cualquier n8n |
| 2 | Settings → Variables → `API_BASE_URL` | Requiere n8n con licencia: Variables es una feature paga |
| 3 | Por defecto: la API pública | Si no configurás nada |

### 4. La API directa

Todo lo que hace el workflow está disponible como endpoints. La referencia completa, con ejemplos que se pueden pegar en una terminal, está en **[`docs/api.md`](docs/api.md)**. Documentación interactiva en **[/docs](https://ciri-chargeback-agent.onrender.com/docs)**.

El camino más corto, una sola llamada que corre el pipeline completo y devuelve el informe:

```bash
curl -X POST "https://ciri-chargeback-agent.onrender.com/api/panel/analyze?direct=true" \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte.html
```

### Todo local con Docker

```bash
git clone https://github.com/federicomoroz/ciri-chargeback-agent.git
cd ciri-chargeback-agent
cp .env.example .env          # ver más abajo qué dos claves poner
docker-compose up -d
```

Hacen falta dos claves, ambas con free tier: [Anthropic](https://console.anthropic.com/settings/keys) para Claude y [Voyage AI](https://dash.voyageai.com/) para los embeddings. Langfuse es opcional, se activa con `CB_LANGFUSE_ENABLED=true`.

Levanta Qdrant, la API y n8n. Se inicializa solo: SQLite se carga desde el Excel y Qdrant se indexa en el primer arranque, sin paso de seed manual. El panel queda en `http://localhost:8000/panel` y n8n en `http://localhost:5678`.

Para que el workflow use tu API local en vez de la pública, mandá `api_base_url` en el body:

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

## La API

Cada endpoint es una herramienta que el orquestador llama por su nombre. La referencia
completa, agrupada por para qué sirve cada uno y con ejemplos que se pegan en una terminal,
está en **[`docs/api.md`](docs/api.md)**.

Documentación interactiva generada por FastAPI: **[/docs](https://ciri-chargeback-agent.onrender.com/docs)**.

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
