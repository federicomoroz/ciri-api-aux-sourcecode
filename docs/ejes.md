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
| Indexación | `api/app/rag/indexer.py` — 2 colecciones Qdrant: `policies` (17 en el dataset, crece por API) e `historical_cases` (60, crece con cada caso avalado) |
| Chunking | **1 política = 1 documento, sin partir.** Las 17 políticas miden entre 60 y 90 tokens cada una: partirlas rompería la unidad semántica (condición + umbral + excepción) sin ganar nada. Ver [`rag_explanation.md#estrategia-de-chunking`](rag_explanation.md#estrategia-de-chunking) |
| Embeddings | `voyage-multilingual-2`, 1024 dims — elegido por rendimiento en español (`decisions.md#5`) |
| Búsqueda semántica | `api/app/rag/retriever.py` — QueryBuilder determinístico + 4 reglas de enriquecimiento + reranking por boosts. El `top_k` de políticas sale de contar la colección: cargar la política 18 no deja a ninguna afuera |
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
| Precedentes | `historical_cases` en Qdrant | Cada caso avalado por un analista y con Judge ≥ 8.0 se reindexa y queda disponible para el siguiente |
| Caché de idempotencia | `report_cache` en SQLite, clave (transacción, VIP) | Repetir un caso devuelve el informe ya generado, sin volver a pagar el LLM |

**Prompt engineering:** 3 prompts versionados en `api/app/llm/prompts/`, cada uno con su
cabecera de versión, fecha y changelog. Documentados en [`prompts.md`](prompts.md).

| Prompt | Versión | Modelo |
|---|---|---|
| `v1_policy_eval.py` | v1.5 | Haiku 4.5 |
| `v1_resolution.py` | v3.2 | Sonnet |
| `v1_judge.py` | v2.2 | Sonnet |

**El modelo de cada paso se elige por separado**, desde el panel, y se guarda en SQLite: `constants.py` tiene el default y cambiarlo no es un deploy. Son tres tareas distintas —comparar contra reglas, redactar, aplicar una rúbrica— y no hay motivo para que compartan modelo por defecto de implementación. Además de Anthropic hay cinco proveedores con free tier soportados vía `OpenAICompatibleClient`, que es la segunda implementación del `Protocol` y la prueba de que cambiar de proveedor no toca ningún llamador. Ver `decisions.md#21`.

---

## 4. Automatización — *Clasificación, derivación, reportes, alertas*

| Sub-eje | Resolución |
|---|---|
| Clasificación | `risk_level` ∈ BLOCKER / HIGH / MEDIUM / LOW, calculado por código (`domain/decision.py::decidir`), no por el LLM |
| Derivación | `Switch — Derivación` en n8n enruta por **`requires_hitl`**, que es lo que decidió la API — no por el nivel de riesgo. Un caso MEDIUM que pide analista frenaba igual antes de llegar al Switch y salía cerrado; ahora frena en el `Wait`, que espera hasta 24 h. El nivel de riesgo dice cuán grave es; quién decide si hace falta una persona es `requires_hitl` |
| Reportes | `POST /api/reports/html` — Jinja2, 9 secciones, formulario HITL condicional. Ejemplos en `docs/HTML_Output_Examples/` |
| Alertas | Dos severidades con dos caminos. **Fallas** (`ERROR`): los tres workflows propagan a `workflow_ciri_errors.json` — el principal desde 3 nodos `Stop and Error`, el formulario desde 1 — que registra la alerta y avisa por mail. **Entradas rechazadas** (`WARN`): el formulario las registra directo, sin marcar la ejecución como fallida ni mandar mail, porque un tipeo no es una falla. `GET /api/alerts/` lista todo |

**El HITL no aprueba por defecto.** Si el plazo vence sin que nadie responda, el caso sale
marcado `PENDING_HITL` y **no** se registra ninguna decisión de analista: antes, la ausencia de
respuesta caía en `APPROVE` y quedaba asentada como si la hubiera tomado una persona. Además,
sólo una decisión humana manda la resolución al feedback, que es lo que la convierte en
precedente — un caso que nadie revisó no puede volverse el ejemplo con el que se resuelve el
siguiente. Fijado por `tests/unit/test_workflows_n8n.py::TestHITL`.

Manejo de errores: los 4 nodos `Propagar → Error Handler` cortan el flujo con contexto, los
nodos HTTP reintentan 3 veces con backoff, y los no críticos siguen con `continueRegularOutput`
en vez de tirar toda la investigación.

**Antes de cortar, cada final de error responde**: 400 si el `transaction_id` está mal formado,
404 si la transacción no existe, 503 si la API no contesta, 502 si falla el modelo o el informe.
Ningún camino devuelve un `200` con el cuerpo vacío, que es indistinguible de un éxito.

`settings.errorWorkflow` viaja en el JSON exportado, **pero su valor es el ID del workflow de
errores en la instancia donde se exportó**. Al importarlo en otra, ese ID no existe: hay que
apuntarlo a mano una vez, en *Settings → Error Workflow*. El README lo pide como paso 2 de la
importación. Decir que funciona apenas se importa sería falso, y es la clase de detalle que
sólo se descubre cuando ya falló algo.

---

## 5. Identificación de fallas — *Patrones de error, comercios problemáticos, inconsistencias de política*

| Sub-eje | Resolución |
|---|---|
| Patrones de error | `analyzer.py::detect_error_patterns` sobre los 150 logs — 8 patrones nombrados (`ErrorPattern`): timeout sistemático del comercio, problema de conectividad, bloqueo por fraude, cargo duplicado, violación de SLA, falla de integración, pago interrumpido por sesión caída, anomalía geográfica |
| Comercios problemáticos | `analyzer.py::merchant_risk_profile` — `cb_ratio`, volumen, flags (`suspended_merchant`, `high_cb_ratio`). **El umbral es relativo a la línea base del corpus**, no un 2% absoluto: sobre una muestra de disputas donde 47 de 100 transacciones tienen contracargo, un umbral de industria marca los quince comercios y no distingue nada. Con la línea base quedan 2 suspendidos, 4 con ratio alto y 9 limpios |
| Clientes con señales | `analyzer.py::client_flags` — reincidencia, anomalía geográfica |
| Inconsistencias de política | El LLM evalúa cada política recuperada y emite veredicto PASS / WARNING / FAIL / BLOCKER con cita; los conflictos quedan visibles en el reporte. Sólo puede emitir BLOCKER la política que lo declara (`puede_bloquear`, columna de SQLite): habilitar una nueva es un `POST`, no un deploy |

**El hallazgo de este eje está medido, no narrado.** [`docs/politicas_vs_dataset.md`](politicas_vs_dataset.md) cruza las 17 políticas contra el dataset y cuenta: 9 se pueden evaluar, 3 sólo en parte, y **5 piden un dato que el dataset no tiene**. Una de ellas —POL-CB-002, que exige comprobante, comunicación y evidencia— es insatisfacible por construcción y su propio texto dice que sin esos elementos el caso no avanza: por eso el agente no cierra ningún caso solo, y eso es cumplir el reglamento, no fallarle. Se reproduce con `python scripts/auditar_politicas.py`.

Los umbrales de todo esto viven en `api/app/domain/constants.py`, en un solo lugar. Ninguno
está en el canvas de n8n.

**Verificar:**
```bash
curl "https://ciri-chargeback-agent.onrender.com/api/merchants/Airbnb/risk"
# {"cb_ratio": 0.75, "flags": ["high_cb_ratio"], "total_transactions": 4, ...}
```

---

## 6. Auto-mejora — *Feedback, detección de alucinaciones, actualización del RAG, versionado de prompts*

Documento completo: [`mejora_continua.md`](mejora_continua.md).

| Sub-eje | Resolución |
|---|---|
| Captura de feedback | `POST /api/feedback` — el analista corrige la resolución. Dos disparadores: el nodo `Registrar Feedback HITL` en n8n y el formulario embebido en el informe. **Los dos mandan la resolución completa y el motivo**, que es lo que permite indexar el caso; sin eso el feedback se registraba pero el precedente nunca nacía |
| Detección de alucinaciones | `services/guardrails.py::antes_del_override` compara lo que propuso el modelo contra la decisión determinística **antes** de sobrescribirla, que es la única ventana en que la contradicción es observable, y la deja registrada en `guardrail_warnings`. Más `guardrails.despues_del_override` sobre los campos que el modelo sí controla (compensación, confianza) |
| Actualización del RAG | Dos caminos: Judge ≥ 8.0 reindexa el caso como precedente, y `PUT /api/policies/{code}` reindexa la política al instante, sin deploy |
| Versionado de prompts | Cabecera de versión + changelog en cada archivo de `llm/prompts/`; la evolución 8.2 → 9.1 del Judge score está trazada en `prompts.md` |

El LLM-as-Judge puntúa cada resolución sobre 5 criterios (1–10). Bajo 7.0 el caso se marca
`LOW_QUALITY` y sigue viaje con la marca puesta; sobre 8.0 se convierte en precedente.

**Medir la calidad, no sólo puntuarla.** `scripts/evaluar.py` corre N casos del dataset por el pipeline completo y escribe a `docs/evaluaciones/` un JSON con el score de cada uno, el promedio por criterio, la configuración usada y el costo. El muestreo lleva semilla fija, así que la misma invocación devuelve la misma muestra: es lo que convierte un número publicado en un número auditable.

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
| Costo | Langfuse traceá cada llamada con tokens y costo; `GET /api/langfuse/stats` los agrega. Un modelo de free tier cuesta **cero**, y así se informa: antes caía en la tarifa de referencia y una corrida gratuita figuraba en $0.10 |
| Tiempos | Latencia por generación en Langfuse; el panel muestra el tiempo de cada paso vía SSE |
| Calidad | El Judge puntúa **cada** resolución y el score se manda a Langfuse como `score` de la traza |

`api/app/observability/tracer.py` define un `Protocol` con tres implementaciones:
`LangfuseTracer`, `TrazadorLocal` —que anota en SQLite cuando no hay claves de Langfuse— y
`NoOpTracer`, para los tests. La observabilidad se activa con `CB_LANGFUSE_ENABLED=true`, y su
ausencia no deja al panel sin metricas ni rompe el pipeline: se sigue midiendo latencia, tokens
y la nota del juez, y el panel declara de donde salen los numeros.

**Que un servicio externo se caiga no tumba el resto.** La API arranca aunque Qdrant no responda:
el panel sigue en pie, los casos demo se sirven completos —no tocan el vector store— y sólo las
búsquedas semánticas fallan, cada una con su propio error. `GET /health` reporta el estado real de
cada dependencia en lugar de devolver un ok genérico. Importa porque el free tier de Qdrant Cloud
suspende los clusters tras una semana sin uso, y la instancia publicada corre en un Render que
reinicia el contenedor cada vez que despierta. Un workflow semanal
(`.github/workflows/keep-alive.yml`) hace una búsqueda real contra el cluster para que no llegue a
suspenderse.

**Verificar:**
```bash
pytest tests/integration/test_arranque_sin_qdrant.py -v   # levanta contra un Qdrant inexistente
```

---

## Bonus de la consigna

| Bonus pedido | Estado |
|---|---|
| Human-in-the-Loop | Nodo `Wait` con formulario propio para casos HIGH (espera 24 h, **falla cerrado**) + formulario embebido en el reporte. Los dos alimentan `POST /api/feedback` |
| LLM-as-a-Judge | `POST /api/analyze/judge`, 5 criterios con rúbricas, prompt v2.2. Dos de los criterios evalúan **la propuesta del modelo**, no la versión ya corregida por el override: sobre la entregada no podían bajar de 10 por construcción |
| Observabilidad | Langfuse (traces, tokens, costo, scores) |
| Caché semántico | **No implementado, y es deliberado.** Hay caché de idempotencia exact-match; cachear por similitud arriesga devolver la resolución de otro caso. Ver `decisions.md#9` |
| SLA y compensación | `analysis/sla.py::CalculadoraDeSLA.check_sla` mide el plazo del **reclamo**, no de la compra: un caso cerrado se mide hasta su cierre, y **sin reclamo registrado no se mide** —`within_sla` queda en `None`—. Entre la compra y el reclamo pueden pasar meses: contarlos daba 489 días de incumplimiento y compensación automática en 53 de las 100 transacciones |
| Guardrails | 6: cuatro registran contradicciones del modelo con la evidencia —incluida la compensación contra el SLA calculado—, dos validan los campos que el modelo sí controla. Más el fail-closed, que cubre dos casos: **sin** veredictos de política —Qdrant caído, JSON irrecuperable— y **con** veredictos cuyo valor no existe. Los dos derivan a un analista en vez de aprobarse solos: un veredicto que dice `BLOCKED` en vez de `BLOCKER` no es evidencia favorable, es evidencia que no se pudo leer |
| Trazabilidad completa | Cada paso es un nodo nombrado en el canvas + traza en Langfuse + audit trail en SQLite |
| Multi-Agent | **No implementado como tal.** Hay 3 roles de LLM separados (evaluador de políticas, sintetizador, juez) con modelos y prompts distintos, pero orquestados explícitamente por n8n, no negociando entre sí |
