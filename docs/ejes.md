# Los 7 ejes de la consigna — dónde está cada uno

La consigna define 7 ejes que el flujo debe contemplar. Esta es la correspondencia entre cada
eje y la parte concreta del sistema que lo resuelve, con la forma de verificarlo.

En el canvas de n8n hay un sticky note al pie con este mismo mapeo, para poder seguirlo sin
salir del workflow.

---

## 1. Ingesta — *Webhook, formulario, API o archivo*

Cuatro puntos de entrada, todos hacia el mismo pipeline:

| Entrada | Dónde |
|---|---|
| Webhook HTTP | `n8n/workflow_ciri_agent.json` → nodo `Webhook — Entrada`, path `/webhook/chargeback-agent` |
| Formulario | `n8n/workflow_ciri_form.json` → Form Trigger nativo de n8n |
| API directa | `POST /api/analyze/resolve` (y el pipeline completo en `POST /api/panel/analyze-stream`) |
| Archivo | `scripts/seed_data.py` y `api/app/data/loader.py` — Excel de 4 hojas → SQLite |

El nodo `Validar Formato — IF` rechaza cualquier `transaction_id` que no matchee `TXN-\d{5}`
antes de gastar un solo token, y deriva al Error Handler.

**Verificar:**
```bash
curl -X POST https://<tu-n8n>/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' -o reporte.html
```

---

## 2. RAG — *Indexación, chunking, embeddings, búsqueda semántica, contexto al LLM*

Documento completo: [`rag_explanation.md`](rag_explanation.md).

| Sub-eje | Resolución |
|---|---|
| Indexación | `api/app/rag/indexer.py` — 2 colecciones Qdrant: `policies` (17, dinámica) e `historical_cases` (60, autocrece) |
| Chunking | **1 política = 1 documento, sin partir.** Las 17 políticas miden entre 60 y 90 tokens cada una: partirlas rompería la unidad semántica (condición + umbral + excepción) sin ganar nada. Ver [`rag_explanation.md#estrategia-de-chunking`](rag_explanation.md#estrategia-de-chunking) |
| Embeddings | `voyage-multilingual-2`, 1024 dims — elegido por rendimiento en español (`decisions.md#5`) |
| Búsqueda semántica | `api/app/rag/retriever.py` — QueryBuilder determinístico + 4 reglas de enriquecimiento + reranking por boosts |
| Contexto al LLM | `api/app/rag/formatter.py` — formatea políticas y precedentes con sus citas antes de entrar al prompt |

Lo que **no** se indexa, y por qué, está documentado igual de explícitamente: transacciones y
logs se buscan por ID exacto, donde la similitud semántica no aporta nada.

**Verificar:**
```bash
curl "https://ciri-chargeback-agent.onrender.com/api/policies/search?motivo=fraude&payment_method=Cripto&fraud_score=8"
# La respuesta incluye query_used: la consulta enriquecida que se armó, no solo los resultados
```

---

## 3. Agente — *Tool calling, memory, prompt engineering*

Este eje se resuelve con una decisión explícita que conviene leer completa en
[`decisions.md#1`](decisions.md), porque se aparta de lo habitual.

**Tool calling: sí, pero determinístico.** El agente tiene 7 herramientas —transacción, logs,
políticas, casos similares, riesgo de comercio, historial de cliente, SLA— y las llama todas,
siempre, en el mismo orden. No usa el nodo AI Agent de n8n ni deja que el LLM elija qué llamar.

El trade-off es deliberado: en una fintech regulada, un auditor tiene que poder reconstruir qué
se consultó para resolver un caso. Un LLM que decide sus tools en runtime es no determinístico
y no auditable. Se pierde flexibilidad; se gana que cada investigación sea reproducible y que
el canvas sea la documentación del proceso. Para 7 fuentes fijas y conocidas, la flexibilidad
no compraba nada.

**Memory: del sistema, no de la conversación.** No hay memoria conversacional porque no hay
conversación — cada contracargo es un caso independiente. La memoria que sí importa es la que
ataca el problema del enunciado ("poca reutilización del conocimiento generado"):

| Mecanismo | Dónde | Efecto |
|---|---|---|
| Precedentes | `historical_cases` en Qdrant | Cada caso resuelto con Judge ≥ 8.0 se reindexa y queda disponible para el siguiente |
| Caché de idempotencia | `report_cache` en SQLite, clave (transacción, VIP) | Repetir un caso devuelve el informe ya generado, sin volver a pagar el LLM |

**Prompt engineering:** 3 prompts versionados en `api/app/llm/prompts/`, cada uno con su
cabecera de versión, fecha y changelog. Documentados en [`prompts.md`](prompts.md).

| Prompt | Versión | Modelo |
|---|---|---|
| `v1_policy_eval.py` | v1.2 | Haiku 4.5 |
| `v1_resolution.py` | v3.0 | Sonnet |
| `v1_judge.py` | v2.0 | Sonnet |

---

## 4. Automatización — *Clasificación, derivación, reportes, alertas*

| Sub-eje | Resolución |
|---|---|
| Clasificación | `risk_level` ∈ BLOCKER / HIGH / MEDIUM / LOW, calculado por código (`resolution.py::_determine_outcome`), no por el LLM |
| Derivación | `Switch — Nivel de Riesgo` en n8n: HIGH va a un `Wait` que espera la aprobación de un analista (HITL); el resto se resuelve solo |
| Reportes | `POST /api/reports/html` — Jinja2, 9 secciones, formulario HITL condicional. Ejemplos en `docs/examples/` |
| Alertas | `workflow_ciri_errors.json` (Error Trigger → `POST /api/alerts/` → email opcional) + `GET /api/alerts/` para el log operativo |

Manejo de errores: los 3 nodos `Propagar → Error Handler` cortan el flujo con contexto, los
nodos HTTP reintentan 3 veces con backoff, y los no críticos siguen con `continueRegularOutput`
en vez de tirar toda la investigación.

---

## 5. Identificación de fallas — *Patrones de error, comercios problemáticos, inconsistencias de política*

| Sub-eje | Resolución |
|---|---|
| Patrones de error | `analyzer.py::detect_error_patterns` sobre los 150 logs — 8 patrones nombrados (`ErrorPattern`): timeout sistemático del comercio, problema de conectividad, bloqueo por fraude, cargo duplicado, violación de SLA, falla de integración, pago interrumpido por sesión caída, anomalía geográfica |
| Comercios problemáticos | `analyzer.py::merchant_risk_profile` — `cb_ratio`, volumen, flags (`suspended_merchant`, `high_cb_ratio`) |
| Clientes con señales | `analyzer.py::client_flags` — reincidencia, anomalía geográfica |
| Inconsistencias de política | El LLM evalúa cada política recuperada y emite veredicto PASS / WARNING / FAIL / BLOCKER con cita; los conflictos quedan visibles en el reporte |

Los umbrales de todo esto viven en `api/app/domain/constants.py`, en un solo lugar. Ninguno
está en el canvas de n8n.

**Verificar:**
```bash
curl "https://ciri-chargeback-agent.onrender.com/api/merchants/Airbnb/risk"
# {"cb_ratio": 0.75, "flags": ["suspended_merchant"], ...}
```

---

## 6. Auto-mejora — *Feedback, detección de alucinaciones, actualización del RAG, versionado de prompts*

Documento completo: [`mejora_continua.md`](mejora_continua.md).

| Sub-eje | Resolución |
|---|---|
| Captura de feedback | `POST /api/feedback` — el analista corrige la resolución; el nodo `Registrar Feedback HITL` lo dispara desde n8n |
| Detección de alucinaciones | `resolution.py::_detect_divergence` compara lo que propuso el modelo contra la decisión determinística **antes** de sobrescribirla, que es la única ventana en que la contradicción es observable, y la deja registrada en `guardrail_warnings`. Más `_validate_resolution` sobre los campos que el modelo sí controla (compensación, confianza) |
| Actualización del RAG | Dos caminos: Judge ≥ 8.0 reindexa el caso como precedente, y `PUT /api/policies/{code}` reindexa la política al instante, sin deploy |
| Versionado de prompts | Cabecera de versión + changelog en cada archivo de `llm/prompts/`; la evolución 8.2 → 9.1 del Judge score está trazada en `prompts.md` |

El LLM-as-Judge puntúa cada resolución sobre 5 criterios (1–10). Bajo 7.0 el caso se marca
`LOW_QUALITY` y sigue viaje con la marca puesta; sobre 8.0 se convierte en precedente.

**Verificar** — editar una política en caliente y ver que el RAG cambia:
```bash
curl -X PUT https://ciri-chargeback-agent.onrender.com/api/policies/POL-FRD-001 \
  -H "Content-Type: application/json" \
  -d '{"description": "El score mínimo ahora es 40..."}'
# Reindexada en Qdrant al instante. Sin redeploy, sin tocar código.
```

---

## 7. Observabilidad — *Errores, costo, tiempos, calidad de respuestas*

| Sub-eje | Resolución |
|---|---|
| Errores | Workflow de error handler + `POST /api/alerts/` + `GET /api/alerts/` (log operativo) |
| Costo | Langfuse traceá cada llamada con tokens y costo; `GET /api/langfuse/stats` los agrega |
| Tiempos | Latencia por generación en Langfuse; el panel muestra el tiempo de cada paso vía SSE |
| Calidad | El Judge puntúa **cada** resolución y el score se manda a Langfuse como `score` de la traza |

`api/app/observability/tracer.py` define un `Protocol` con dos implementaciones: `LangfuseTracer`
y `NoOpTracer`. La observabilidad se activa con `CB_LANGFUSE_ENABLED=true` y su ausencia nunca
rompe el pipeline ni los tests.

---

## Bonus de la consigna

| Bonus pedido | Estado |
|---|---|
| Human-in-the-Loop | Nodo `Wait` para casos HIGH + formulario embebido en el reporte |
| LLM-as-a-Judge | `POST /api/analyze/judge`, 5 criterios con rúbricas, prompt v2.0 |
| Observabilidad | Langfuse (traces, tokens, costo, scores) |
| Caché semántico | **No implementado, y es deliberado.** Hay caché de idempotencia exact-match; cachear por similitud arriesga devolver la resolución de otro caso. Ver `decisions.md#9` |
| Guardrails | 5: tres registran contradicciones del modelo con la evidencia, dos validan los campos que el modelo sí controla |
| Trazabilidad completa | Cada paso es un nodo nombrado en el canvas + traza en Langfuse + audit trail en SQLite |
| Multi-Agent | **No implementado como tal.** Hay 3 roles de LLM separados (evaluador de políticas, sintetizador, juez) con modelos y prompts distintos, pero orquestados explícitamente por n8n, no negociando entre sí |
