# Decisiones Técnicas — CIRI Chargeback Agent

Este documento explica el **por qué** detrás de cada decisión técnica importante. Formato: Contexto → Decisión → Razonamiento → Trade-offs → Qué haría distinto en producción.

Una nota sobre el stack: este proyecto fue construido con las restricciones reales de un free tier: n8n Cloud (trial), Render (free tier con cold starts de ~50s), Qdrant Cloud (1GB free), Voyage AI (free tier). Cada decisión refleja ese contexto — no tuve acceso a infraestructura dedicada, pero el diseño está pensado para escalar cuando lo haya.

---

## 1. Orquestación explícita con n8n (no AI Agent)

**Contexto:** El sistema necesita un orquestador para las investigaciones de contracargos. n8n tiene un nodo "AI Agent" que le da al LLM control sobre qué tools llamar y en qué orden. La alternativa es orquestación explícita con nodos nombrados.

**Decisión:** Usar 45 nodos explícitos en n8n (39 ejecutables + 6 sticky notes) con HTTP Request, Set, Switch, Wait. Sin nodo AI Agent, sin tool-calling del LLM en el workflow.

**Razonamiento:** En una fintech, un regulador o un oficial de compliance necesita ver exactamente qué pasó, en qué orden, para cada caso. Un AI Agent es una caja negra — el LLM decide el flujo en runtime, lo que lo hace no-determinístico e imposible de auditar. Con nodos explícitos, cada investigación sigue los mismos pasos en el mismo orden: 7 llamadas de contexto → evaluación de políticas → síntesis de resolución → Judge → ruteo por riesgo. El flujo queda visible en el canvas de n8n, versionado como JSON, y es reproducible.

Elegí n8n porque CIRI lo usa como herramienta diaria. Los nodos nativos (Switch, Wait, IF, Merge, Code) se usan para el control de flujo y el armado de payloads; ninguna regla de negocio vive en el canvas. Cuando un paso implica una decisión de negocio — el SLA que aplica según país, si un comercio está suspendido, si un cliente es reincidente — es un HTTP Request a FastAPI, que lee los umbrales de `domain/constants.py`. Es la única forma de que editar una política no implique editar el workflow.

**Trade-offs:**
- (+) Cada paso es visible y auditable en el canvas
- (+) Ejecución determinística — mismos pasos, mismo orden, siempre
- (+) Agregar una fuente de datos nueva = un HTTP Request + un Set
- (-) Menos flexible — branches condicionales requieren wiring manual
- (-) Más nodos que mantener visualmente

**En producción:** Nada cambiaría en lo fundamental. Agregaría métricas por step (Prometheus) y una dead-letter queue para investigaciones fallidas.

---

## 2. Políticas como datos, no como código

**Contexto:** El sistema evalúa 17+ políticas de contracargos (umbrales de fraude, blocker de cripto, reglas de SLA). Estas políticas cambian con las regulaciones. Lo tradicional es hardcodear reglas en condicionales de Python.

**Decisión:** Almacenar políticas como documentos Markdown en Qdrant (para retrieval semántico) y como filas en SQLite (para lookup exacto). El LLM evalúa las políticas contra la transacción — no código Python determinístico.

**Razonamiento:** Si un analista puede editar una política vía `PUT /api/policies/{code}` y el sistema la usa inmediatamente (Qdrant se re-indexa en cada escritura, sin redeploy), entonces la evaluación también tiene que ser dinámica. Un LLM puede leer una política en lenguaje natural y aplicarla a una transacción — algo que un `if` hardcodeado no puede hacer para texto de política arbitrario.

Esto también significa que expertos de dominio pueden redactar políticas sin escribir código. En CIRI, donde las políticas de contracargos cambian frecuentemente, esto es crítico.

**Trade-offs:**
- (+) Actualizaciones de política sin downtime — editar, guardar, efectivo inmediatamente
- (+) Expertos de dominio pueden redactar políticas en lenguaje natural
- (+) Nuevos tipos de política no requieren cambios de código
- (-) La evaluación LLM cuesta tokens por política por investigación
- (-) El LLM puede alucinar un veredicto — mitigado por guardrails post-LLM

**En producción:** Agregaría versionado de políticas (historial tipo git con rollback), A/B testing de versiones de políticas, y un pre-filtro determinístico para políticas triviales (ej: cripto blocker) para ahorrar tokens.

---

## 3. QueryBuilder determinístico para RAG

**Contexto:** El sistema recupera políticas y casos históricos relevantes de Qdrant antes de la evaluación LLM. El query a Qdrant podría generarse por LLM o por reglas determinísticas.

**Decisión:** Usar un `QueryBuilder` basado en reglas que construye queries de Qdrant a partir de campos estructurados de la transacción. Ejemplo: un pago Cripto siempre agrega `"criptomonedas no reversible blocker"`; fraud_score < 30 agrega `"alto riesgo fraude score bajo"`.

**Razonamiento:** Tres razones: (1) **Costo** — cero tokens gastados en generación de queries, (2) **Reproducibilidad** — misma transacción siempre genera el mismo query, facilitando debugging, (3) **Velocidad** — ahorra un round-trip de LLM por investigación. Con Voyage AI en free tier y Qdrant Cloud free, cada token y cada milisegundo cuenta.

Las reglas de enriquecimiento codifican conocimiento de dominio (ej: "pagos cripto son irreversibles" siempre es contexto relevante para retrieval). Además, hay reranking determinístico: boost de 0.05 por método de pago coincidente y 0.03 por país.

**Trade-offs:**
- (+) Gratis — cero costo de tokens para construir queries
- (+) Determinístico — misma entrada siempre produce el mismo query
- (+) Rápido — sin latencia de LLM para este paso
- (-) Requiere mantenimiento manual cuando emergen nuevos patrones
- (-) Puede perder documentos que un query creativo de LLM encontraría

**En producción:** Agregaría un feedback loop — cuando el Judge da score bajo y las políticas recuperadas parecen incompletas, loguear el gap. También consideraría un modo híbrido: query determinístico + expansión LLM opcional para edge cases.

---

## 4. Arquitectura de capas de servicio

**Contexto:** Las rutas de FastAPI manejan requests HTTP. La pregunta es dónde poner la lógica de negocio.

**Decisión:** Separación en tres capas:
- **Routes** (~20 líneas cada una) — solo HTTP: parsear request, llamar servicio, devolver response. Todos los módulos de ruta incluyen `logger = logging.getLogger(__name__)` para observabilidad consistente.
- **Services** (`ResolutionService`, `FeedbackService`, `PipelineService`) — orquestan múltiples pasos (llamadas LLM, guardrails, caching). `PipelineService` usa métodos compartidos (`_submit_context_futures`, `_resolve`, `_judge`, `_build_report_data`) entre el modo síncrono y el streaming SSE, eliminando duplicación.
- **Analyzer** (`analysis/analyzer.py`) — lógica de negocio pura: reglas SLA, flags de riesgo, patrones de error

El acceso a datos está aislado en `data/db.py`. Las definiciones de dominio (models, enums, 73+ constants) tienen cero dependencias externas. Todos los magic numbers y strings del dominio están centralizados en `constants.py` — umbrales de pipeline, tipos de alertas, prefijos de guardrails, nombres de templates.

**Razonamiento:** Esto hace que cada capa sea testeable independientemente. Los tests unitarios mockean solo la capa de abajo. Las rutas se testean con `TestClient` y servicios mock. Los servicios se testean con clientes LLM mock. El analyzer son funciones puras — sin mocks.

Con 666 tests pasando (633 unit/integration + 33 E2E contra la API real), esta arquitectura demostró ser robusta para iterar rápido sin romper cosas.

**Trade-offs:**
- (+) Cada capa tiene una sola responsabilidad
- (+) Tests unitarios son rápidos y enfocados
- (+) Fácil de intercambiar implementaciones (ej: distinto proveedor LLM)
- (+) Timeouts explícitos en todas las llamadas I/O externas (Anthropic, Qdrant, Voyage AI)
- (-) Más archivos para navegar
- (-) Operaciones simples cruzan 3 capas

**En producción:** Agregaría CQRS — modelos de lectura separados (dashboard, reporting) de modelos de escritura (feedback, actualizaciones de política).

---

## 5. Embeddings Voyage AI (`voyage-multilingual-2`, 1024d)

**Contexto:** El sistema necesita embeddings para búsqueda semántica en Qdrant. Las políticas y casos están en español. Opciones: modelo local (sentence-transformers), OpenAI embeddings, o Voyage AI.

**Decisión:** Usar `voyage-multilingual-2` de Voyage AI (1024 dimensiones) vía API.

**Razonamiento:** Tres factores: (1) **Calidad multilingüe** — `voyage-multilingual-2` consistentemente benchmarkea top-3 para retrieval de texto en español en MTEB, superando a `text-embedding-3-small` y modelos locales tipo `paraphrase-multilingual-MiniLM-L12-v2`, (2) **Free tier** — Voyage AI ofrece un free tier generoso, suficiente para este proyecto, (3) **1024 dimensiones** — buen balance entre calidad y costo de almacenamiento/búsqueda en Qdrant.

El free tier fue un factor decisivo. Para una prueba técnica no tiene sentido pagar por embeddings cuando hay una opción de igual o mejor calidad gratuita.

**Trade-offs:**
- (+) Embeddings multilingües best-in-class para español
- (+) Free tier suficiente para esta escala
- (+) API-based — sin GPU, sin descarga de modelo, sin OOM
- (-) Dependencia externa — latencia API + riesgo de disponibilidad
- (-) Vendor lock-in en dimensiones de embedding (migrar requiere re-indexar)

**En producción:** Agregaría un cache de embeddings (hash de texto → vector cacheado) para reducir llamadas API en documentos re-indexados. También un modelo local como fallback para desarrollo offline.

---

## 6. SQLite en vez de Postgres

**Contexto:** El sistema necesita almacenamiento estructurado para transacciones, casos, logs, políticas y feedback.

**Decisión:** Usar SQLite con queries parametrizadas. Un archivo `.db`, sin proceso servidor, cero configuración.

**Razonamiento:** Para una evaluación técnica con ~100 transacciones y ~60 casos, SQLite es la opción pragmática. Elimina un componente de infraestructura completo (servidor de DB), simplifica Docker Compose, y hace el proyecto self-contained. En Render free tier, SQLite es la opción natural — no hay Postgres managed sin costo.

La capa de acceso a datos (`db.py`) usa SQL estándar con queries parametrizadas — migrar a Postgres requeriría solo cambiar el connection string y ajustes menores de dialecto.

**Trade-offs:**
- (+) Cero configuración — sin servidor, sin credenciales, sin networking
- (+) Self-contained — toda la base de datos es un archivo
- (+) Portable — funciona en cualquier OS
- (-) Single-writer — sin soporte de escritura concurrente
- (-) Sin JSONB, CTEs con window functions, ni indexación avanzada
- (-) En Render free tier, el filesystem es efímero — la DB se recrea del Excel en cada cold start

**En producción:** Migrar a PostgreSQL para acceso concurrente, columnas JSONB para metadata flexible de políticas, y workflows de backup/restore apropiados.

---

## 7. Guardrails post-LLM + overrides determinísticos

**Contexto:** El LLM genera recomendaciones de resolución (APPROVE/REJECT/ESCALATE). Los LLMs pueden alucinar — ej: recomendar APPROVE cuando hay un BLOCKER activo.

**Decisión:** Dos mecanismos complementarios:

**Overrides determinísticos** (el código siempre gana):
- 6 de 11 campos de la resolución son calculados por Python y sobreescriben lo que diga el LLM: `recommended_action`, `risk_level`, `risk_reason`, `requires_hitl`, `precedent_summary`, `policy_verdicts`
- La whitelist de BLOCKER: solo `POL-EXC-003` (cripto) puede producir veredictos BLOCKER. Cualquier otro BLOCKER del LLM se degrada a FAIL automáticamente.

**Guardrails de validación** (detección de inconsistencias):
1. APPROVE + BLOCKER → auto-corrección a REJECT + flag de alucinación
2. REJECT sin BLOCKER → auto-corrección a PENDING_HITL
3. Compensación excesiva (> 110% del monto) → flag para revisión
4. Confianza excesiva (> 0.95 con 2+ FAILs) → flag como sospechoso

**Razonamiento:** La filosofía es "el código decide, el LLM explica." El sistema de guardrails de la consigna es uno de los ejes explícitos, y me pareció fundamental que las decisiones críticas (acción, nivel de riesgo, escalamiento) no dependan de que el LLM interprete correctamente. Python calcula la acción basándose en los veredictos de política; el LLM llena los campos de texto (justificación, next_steps, log_summary).

**Trade-offs:**
- (+) Atrapa los errores de LLM de mayor impacto
- (+) Cero latencia — checks puros de Python
- (+) Auto-corrección para el caso más crítico (APPROVE + BLOCKER)
- (-) Umbrales hardcodeados (110%, 0.95) — pueden necesitar tuning
- (-) No puede atrapar errores semánticos (ej: política citada incorrectamente)

**En producción:** Agregaría más guardrails: verificación cruzada de códigos de política citados contra las efectivamente recuperadas, y logging de cada trigger de guardrail a una tabla de auditoría dedicada.

---

## 8. Judge a través de FastAPI (no API directa de Anthropic)

**Contexto:** El LLM-as-Judge evalúa la calidad de cada resolución en 5 criterios. Inicialmente, n8n llamaba a la API de Anthropic directamente vía HTTP Request. Después lo cambié para que pase por FastAPI (`POST /api/analyze/judge`).

**Decisión:** Rutear el Judge por FastAPI, donde el prompt, modelo y parsing están gestionados en código Python (`v2_judge.py` + `ResolutionService`).

**Razonamiento:** Tres beneficios: (1) **Versionado de prompts** — el prompt del Judge vive en `v1_judge.py`, versionado junto al código que lo usa. Inlinearlo en un body de HTTP Request de n8n lo hace invisible al code review. (2) **Observabilidad** — Langfuse captura la llamada completa del Judge (tokens, latencia, costo) junto con la resolución en el mismo trace. (3) **Consistencia** — todas las llamadas LLM pasan por el mismo `AnthropicClient` con el mismo error handling, retry logic y configuración.

Esto también me permitió iterar rápido en el prompt del Judge. La versión 2.0 con rubrics granulares (5 niveles por criterio) fue clave para romper el techo de 8.6 que tenía con la versión anterior.

**Trade-offs:**
- (+) Prompt versionado en Python, no enterrado en JSON de n8n
- (+) Observabilidad completa en Langfuse para cada llamada LLM
- (+) Consistente error handling en todas las operaciones LLM
- (-) Un hop de red extra (n8n → FastAPI → Anthropic en vez de n8n → Anthropic)
- (-) FastAPI se vuelve single point of failure para todas las llamadas LLM

**En producción:** Circuit breaker en FastAPI para fallos de Anthropic. Judge asíncrono — no necesita bloquear la respuesta.

---

## 9. Caché de idempotencia exacto, no semántico

**Contexto:** Repetir la investigación del mismo contracargo cuesta dos llamadas a Sonnet y más de cien segundos. Hace falta no volver a pagar por un caso ya resuelto. La opción atractiva es un caché semántico: embeber la consulta, buscar en Qdrant y, si hay algo por encima de cierta similitud, devolver esa resolución.

**Decisión:** Caché exact-match en SQLite, con clave `(transaction_id, cliente_vip)`. Sin caché semántico.

**Razonamiento:** El caché semántico responde una pregunta distinta de la que hay que responder. Dos contracargos pueden ser 0.95 similares —mismo comercio, mismo método de pago, montos parecidos— y merecer resoluciones opuestas, porque lo que decide el caso son los veredictos de política sobre *esa* transacción y el historial de *ese* cliente. Devolver la resolución de otro caso porque se parece es exactamente el tipo de error que no se puede cometer en un contracargo: el informe llevaría el identificador correcto y el razonamiento de otro.

El umbral no arregla eso. Subirlo hasta que sea seguro lo convierte, en la práctica, en una comparación exacta — que es lo que ya hace SQLite, sin costo de embedding, sin latencia de búsqueda vectorial y sin la posibilidad de equivocarse.

La ganancia real —no repetir trabajo— la da igual el caché exacto: la misma transacción consultada dos veces devuelve el informe ya generado en milisegundos.

**Trade-offs:**
- (+) Un acierto no puede devolver la resolución de otro caso: la clave es la transacción
- (+) Cero llamadas a la API de embeddings y cero latencia de búsqueda vectorial
- (+) La invalidación es trivial y explícita: la clave es el caso
- (-) No aprovecha casos parecidos, que es justamente lo que un caché semántico prometía
- (-) La tasa de acierto depende de que se repitan consultas sobre la misma transacción

**En producción:** TTL sobre las entradas para que un cambio de política no siga sirviendo informes viejos, e invalidación explícita al editar una política. Si algún día se quisiera aprovechar casos parecidos, el lugar correcto no es la resolución final sino la evaluación de políticas, que sí depende del texto de la política y no de la transacción — y aun ahí habría que versionar el caché por hash de la política.

---

## 10. Modelo dual: Haiku para evaluación, Sonnet para síntesis y Judge

**Contexto:** El pipeline tiene 3 llamadas LLM: evaluación de políticas (Call 1), síntesis de resolución (Call 2), y Judge de calidad (Call 3). Usar Sonnet para todo es caro; usar Haiku para todo limita la calidad.

**Decisión:** Modelo dual configurable vía `CB_LLM_MODEL_RESOLUTION`:
- **Call 1 (Policy Eval):** Haiku — evaluación estructurada, input/output bien definido
- **Call 2 (Synthesis):** Sonnet — razonamiento analítico, conexión de evidencias
- **Call 3 (Judge):** Sonnet — discriminación de calidad, rubrics granulares

**Razonamiento:** Empecé con Haiku para todo. El Judge promediaba 8.2/10 y no subía. Probé iterar el prompt del resolution durante 5+ rondas — mismo 8.2. Identifiqué dos cuellos de botella: (1) Haiku no tiene la capacidad analítica para generar justificaciones con profundidad ("Haiku = robot, copia datos, no razona"), (2) Haiku como Judge tiene un techo de scoring en ~8.6 — siempre encuentra 3 debilidades y asigna los mismos scores.

Cambiar Call 2 a Sonnet subió el score a 8.6. Cambiar Call 3 a Sonnet subió a 8.9. Con fixes puntuales llegué a 9.1.

El costo adicional de Sonnet es manejable: Call 2 y Call 3 juntos son ~3-4K tokens de output, que a precios de Sonnet son ~$0.05 por investigación. Call 1 en Haiku mantiene el costo bajo para la parte más voluminosa (17 evaluaciones de política).

**Trade-offs:**
- (+) Mejor calidad de resolución (9.1 vs 8.2 promedio)
- (+) Judge con mejor discriminación — scores más granulares y feedback más accionable
- (+) Call 1 en Haiku mantiene costos controlados
- (-) 2 clientes LLM que gestionar (pero la config es una env var)
- (-) Más costoso que Haiku puro (~3x para Calls 2+3)

**En producción:** Consideraría Haiku para los 3 calls en modo "alto volumen" (donde el costo importa más que la calidad individual) y Sonnet para casos de alto valor o cuando el Judge previo dio score bajo. La configuración ya es una env var, así que el switch es instantáneo.

---

## 11. Data Tables de n8n descartadas para el dataset estructurado

**Contexto:** n8n incorporó Data Tables: almacenamiento tabular dentro de la propia instancia, con import por CSV, nodo dedicado y API. Es la opción natural para alguien que trabaja en n8n a diario, y la alternativa obvia a cargar el Excel en SQLite desde Python. La evalué explícitamente antes de descartarla.

**Decisión:** Mantener SQLite como almacén estructurado, seedeado por `api/app/data/loader.py` desde el Excel. No usar Data Tables.

**Razonamiento:** Data Tables soporta CRUD por fila y filtros con operadores de comparación (`Equals`, `Not Equals`, `Greater Than`, `Less Than`, `Is Empty`…). No soporta agregaciones ni joins. Tres de las herramientas del agente son justamente agregaciones:

| Herramienta | Qué calcula |
|---|---|
| `GET /api/merchants/{name}/risk` | `cb_ratio` = contracargos / transacciones del comercio, volumen total, flags |
| `GET /api/clients/{id}/history` | Reincidencia, países distintos usados, métodos de pago |
| `GET /api/logs/{tx_id}` + `detect_error_patterns` | Conteo por severidad y detección de 8 patrones sobre el set de eventos |

Sin `COUNT` ni `GROUP BY`, la única salida sería traer las 100 transacciones al workflow y agregarlas en un Code node. Eso reintroduce lógica de negocio en el canvas — precisamente lo contrario a la decisión #1 y a lo que la consigna pide.

Hay tres razones más, todas prácticas:

1. **Las Data Tables no viajan en el JSON exportado.** El entregable es un flujo importable; quien lo importe recibiría tablas vacías y tendría que cargar 4 CSVs a mano antes de poder disparar un caso. Hoy el workflow se importa y corre. El dataset viaja en el repo y se seedea solo en el primer arranque.
2. **FastAPI dejaría de poder leer los datos.** Los servicios, el panel y los tests consultan SQLite directo. Con Data Tables la verdad estructurada queda dentro de n8n y todo tendría que salir y volver por su API, para terminar en el mismo lugar con un salto de red más.
3. **El import es por CSV, no por Excel.** El dataset tiene una fila 1 decorativa y nombres de hoja con emojis. Exportar 4 CSVs a mano es un paso manual y no reproducible; `loader.py` lo resuelve determinísticamente y está cubierto por `test_data_loader.py`.

**Dónde sí encajarían:** el caché de idempotencia. "Guardar marcadores para evitar ejecuciones duplicadas" es un caso de uso textual de la feature, y es exactamente lo que hace `GET /api/cache/lookup`. No lo moví porque partiría el estado entre n8n y la API sin ganar nada: hoy el caché vive junto al resto de la verdad estructurada, en la misma transacción y con los mismos tests.

**Trade-offs:**
- (+) Las agregaciones se resuelven donde corresponde, en SQL, no en el canvas
- (+) El workflow se importa y corre sin cargar datos a mano
- (+) El dataset queda versionado en el repo, no en el estado de una instancia
- (-) Una feature nativa de n8n sin usar, en un puesto donde n8n es la herramienta del día a día
- (-) Un componente más (SQLite) que si todo viviera dentro de n8n

**En producción:** el argumento se refuerza. El almacén estructurado sería Postgres con las agregaciones como vistas materializadas o índices, no un almacén interno de la herramienta de orquestación limitado a 200 MiB por instancia. Data Tables las usaría para lo que están pensadas: estado operativo del propio workflow — marcadores de deduplicación, flags de feature, datos de evaluación — no como base de datos del dominio.

---

## 12. Deuda tecnica asumida: el panel de testing

**Contexto:** `api/app/reports/templates/test_panel.html` tiene 3112 lineas, con todo el CSS y todo el JavaScript embebidos en un solo archivo. Es el archivo mas grande del proyecto: cuatro veces el modulo Python mas extenso.

**Decision:** Dejarlo asi para esta entrega, y anotarlo.

**Razonamiento:** El panel no es un entregable de la consigna — es un extra para que se pueda probar el sistema sin montar nada. Partirlo en CSS, JavaScript y plantilla obliga a servir estaticos, que hoy no existen, y el beneficio recae sobre codigo que nadie va a mantener despues de la evaluacion. El costo de equivocarse ahi, ademas, es alto: si el panel se rompe, el evaluador se queda sin la forma mas rapida de ver el sistema andando.

Los archivos que si son entregables — el workflow, la API, los informes — quedaron por debajo de las 100 lineas por funcion despues de la auditoria.

**Trade-offs:**
- (+) Cero riesgo sobre la unica pieza que el evaluador va a tocar sin instalar nada
- (+) El esfuerzo se concentro donde se evalua la arquitectura
- (-) 3112 lineas que nadie va a querer tocar
- (-) Sin cache de estaticos: el navegador se baja el CSS y el JS en cada carga

**En produccion:** separar en `static/panel.css` y `static/panel.js`, servirlos con `StaticFiles` y versionarlos por hash para poder cachearlos. Es media jornada, pero recien vale la pena cuando el panel deje de ser una herramienta de demostracion.

---

## 13. Un solo tipo para el contexto del caso

**Contexto:** La misma informacion —transaccion, logs, politicas, precedentes, riesgo del comercio, historial del cliente— estaba modelada tres veces: `ResolveRequest` para lo que manda n8n, `_PipelineContext` para el pipeline directo y `ReportRequest` para el informe. Con tres nombres distintos para lo mismo: la transaccion era `tx_data`, `tx` o `transaction`; el historial del cliente, `client_history` o `client_profile`.

**Decision:** Un `CaseContext` interno (`domain/context.py`). Los modelos de entrada y salida conservan sus nombres, porque son contratos que n8n ya consume, pero se traducen a el en la frontera.

**Razonamiento:** `ResolutionService.resolve()` recibia ocho parametros posicionales que siempre viajaban juntos. Eso es un unico concepto disfrazado de lista de argumentos: agregar una fuente de contexto obligaba a tocar la firma, los dos llamadores y todos los tests. Ahora se agrega un campo al contexto y listo.

Traducir en la frontera en vez de renombrar los campos del contrato mantiene la compatibilidad con el workflow que ya esta en manos del evaluador.

**Trade-offs:**
- (+) Una firma en vez de ocho parametros; agregar contexto no propaga cambios
- (+) Los nombres internos dejan de contradecirse entre modulos
- (+) `CaseContext` es inmutable: se arma una vez y viaja
- (-) Una traduccion mas entre el modelo de entrada y el tipo interno
- (-) `ReportRequest` sigue con sus propios nombres, porque es contrato de salida

---

## 14. Modo demo: el sistema se evalúa sin gastar la cuenta de nadie

**Contexto:** Investigar un caso cuesta dinero real —Haiku evalúa cada política recuperada, Sonnet sintetiza, Sonnet vuelve a correr como Juez—. Esto es una prueba técnica que se entrega para que alguien la mire, y esa persona no debería tener que consumir la cuenta de otro, ni cargar una clave propia, sólo para ver si el sistema funciona.

El problema es concreto: si la clave del servidor se queda sin crédito, quien abre el panel ve un error y se va con la idea de que el sistema está roto.

**Decisión:** Un modo demo, encendido por defecto (`CB_DEMO_MODE`), con un toggle en el panel. En ese modo **no se llama al modelo** —no es que intente y falle: no gasta—. Los casos de ejemplo viajan con su análisis ya calculado:

| | |
|---|---|
| `data/informes_demo/report_*.html` | El informe completo, tal como se generó |
| `data/informes_demo/analisis_*.json` | La resolución, la evaluación del Juez y los atributos del caso |

Con el JSON, `POST /api/analyze/resolve` y `POST /api/analyze/judge` responden sin modelo, y **el workflow de n8n corre entero**: las siete consultas de contexto son reales, el compilado es real y el informe se genera de verdad. Lo único pregrabado es lo que hubiera contestado el modelo.

Quien manda `api_key` en la petición corre el pipeline completo con su propia cuenta, y el modo demo no le aplica.

**Un caso que no está guardado recibe el más cercano en riesgo.** La comparación es por score antifraude y nada más: es la única medida de riesgo disponible sin correr el pipeline, y es la que decide POL-FRD-001. Meterle método de pago o país sería comparar otra cosa.

**Razonamiento:** La alternativa era consultar el saldo antes de gastar, pero Anthropic no expone el crédito restante en su API —el Admin API reporta consumo, no saldo—. Y aunque lo expusiera, "intentar y caer parado" sigue costando la llamada fallida. No llamar es más barato y más simple.

Lo que hace que esto sea honesto y no un truco es que se declara por todos lados, siempre:

- el HTML abre con un cartel **DEMO (Caso prearmado)**
- la respuesta lleva la cabecera `X-Modo-Demo` y `cost_usd: 0.0`
- el servidor deja un `WARNING` en el log
- cuando el caso mostrado no es el pedido, el cartel **nombra las dos transacciones**

**Lo que deliberadamente no hace: mezclar.** Si `resolve` devolviera la resolución de TXN-00051 y n8n armara el informe con los datos de TXN-00004, saldría un documento con el encabezado de una transacción y los veredictos de otra: se lee como verdadero y no lo es. Por eso la sustitución es del informe entero —el marcador `demo_ejemplo_de` viaja en la resolución y `/api/reports/html` responde con el informe completo del caso prestado—. Un informe prearmado nunca se hace pasar por un análisis recién hecho.

**Trade-offs:**
- (+) El sistema se evalúa de punta a punta, incluido n8n, sin clave y sin costo
- (+) El toggle deja elegir: mirar sin gastar, o correr de verdad con tu cuenta
- (+) La marca viaja en el HTML, en la cabecera, en el uso y en el log: no hay forma de confundirse
- (-) Sólo tres casos tienen análisis propio; el resto recibe el más cercano en riesgo
- (-) El análisis guardado envejece: si cambian los prompts o las políticas, deja de reflejar lo que haría el sistema hoy
- (-) Un archivo más que mantener por cada caso de ejemplo que se agregue

---

## 15. La compensación la decide el SLA, no el modelo

**Decisión:** `POST /api/sla/check` entra al contexto de la resolución, y
`compensation_applicable` / `compensation_amount_usd` los calcula
`ResolutionService._determine_compensation()`. El modelo los recibe ya resueltos en la sección
`DECISION DETERMINADA` y su trabajo es explicarlos.

**Razonamiento:** El workflow ya llamaba a `/api/sla/check`, pero `ResolveRequest` no tenía campo
`sla`, así que Pydantic descartaba el resultado en silencio. La llamada se pagaba, el cálculo era
correcto —días hábiles reales, con tests que fijan los fines de semana— y no llegaba a ningún
lado. Mientras tanto el prompt ordenaba «`compensation_applicable` es true SOLO si se incumplió el
SLA (POL-SLA-004)» sin darle ningún dato de SLA: se le pedía una decisión a ciegas.

Comparar una fecha contra un umbral es exactamente lo que el código hace mejor que un modelo. Una
vez que el dato llega, dejar la decisión en manos del LLM sería elegir la peor de las dos opciones
disponibles. Así que la compensación se suma a los campos determinísticos —pasan de 6 a 8 sobre
11— y aparece en el prompt como dato, no como pregunta.

El guardrail correspondiente corre **antes** del override, igual que los otros tres: si el modelo
propone una compensación que el SLA desmiente, queda registrado en `guardrail_warnings` en vez de
corregirse en silencio.

**Trade-offs:**
- (+) Deja de pagarse una llamada cuyo resultado se tiraba
- (+) Un campo con consecuencia monetaria sale de un cálculo auditable, no de una inferencia
- (+) La divergencia entre lo que propuso el modelo y lo que dice el SLA queda asentada
- (-) Sin dato de SLA el sistema vuelve al valor del modelo: es el caso degradado, no el normal
- (-) El tope de POL-SLA-004 queda en `constants.py`; cambiarlo sigue siendo un deploy, a
  diferencia de las políticas, que son datos

---

## 16. El HITL falla cerrado

**Decisión:** El nodo `Wait — Aprobación HITL` expone un formulario propio de n8n y espera hasta
24 horas. Si el plazo vence sin respuesta, el caso sale marcado `PENDING_HITL` con
`analyst_decision: "SIN_RESPUESTA"`, y la resolución **no** se manda al feedback.

**Razonamiento:** La versión anterior tenía el `Wait` sin parámetros. Sin `resume`, n8n aplica su
default —*After Time Interval*—, así que el nodo no era una compuerta sino una pausa: nadie podía
intervenir. Y aguas abajo, `Procesar Respuesta HITL` resolvía la decisión con
`resume['Decisión'] || resume.decision || 'APPROVE'`. Como el Wait reanudaba por tiempo y ninguna
de las dos claves existía, el default no era el caso excepcional: era el único. Cada contracargo
de riesgo alto se aprobaba solo, y `POST /api/feedback` lo registraba como si lo hubiera aprobado
una persona.

En una fintech regulada eso es el peor de los dos errores posibles. Un caso que queda pendiente
cuesta tiempo; un caso aprobado sin revisar cuesta plata y ensucia la traza de auditoría. Ante la
ausencia de una decisión, la respuesta correcta es no tomar ninguna.

De ahí se sigue la segunda mitad: sólo una decisión humana manda la resolución a `/api/feedback`,
que es lo que la convierte en precedente. Un caso que nadie revisó no puede volverse el ejemplo
con el que se resuelve el siguiente.

**Trade-offs:**
- (+) Ningún caso de riesgo alto se resuelve sin que una persona lo haya visto
- (+) La traza distingue «aprobado por un analista» de «nadie contestó a tiempo»
- (+) El corpus de precedentes sólo crece con casos avalados
- (-) Un plazo vencido deja el caso sin resolver: hace falta un proceso que recoja los pendientes
- (-) Las 24 horas son una constante del canvas, no de `constants.py` — es un parámetro de
  operación del workflow, no una regla de negocio, pero la frontera acá es discutible

---

## 17. Los umbrales se calibran contra los datos, no contra la industria

**Decisión:** El flag de comercio problemático compara el `cb_ratio` contra la **línea base del
propio corpus** (`MERCHANT_SUSPENDED_VS_BASELINE = 1.5`) en vez de contra un 2% absoluto. Y el
SLA se mide **desde que el reclamo se abrió hasta que se cerró**, no hasta hoy.

**Razonamiento:** Los dos umbrales venían de la industria y ninguno se había probado contra el
dataset. El resultado, cuando se miró:

| | Antes | Ahora |
|---|---|---|
| Comercios marcados | **15 de 15** | 2 suspendidos, 4 ratio alto, 9 limpios |
| Casos dentro de SLA | **0 de 60** | 17 de 60 |

Un 2% de contracargos es alto sobre el libro de ventas completo de un comercio. Este dataset no
es eso: es una muestra de disputas donde 60 de 100 transacciones terminaron en contracargo. Medir
una muestra sesgada con la vara de la población marca todo. Y un flag que da positivo el 100% de
las veces no es conservador — no informa nada, y acá además arrastraba cada caso a riesgo HIGH,
dejando muertas las ramas MEDIUM y LOW del enrutador.

Lo mismo con el SLA: comparaba la fecha de la **transacción** contra `now()`. Sobre datos de 2024
eso da 420 días hábiles y el plazo incumplido siempre, con la compensación de POL-SLA-004
disparándose en cada caso. Dos errores encadenados: se medía la compra en vez del reclamo, y se
medía hasta hoy en vez de hasta el cierre. El reloj de un reclamo corre mientras el reclamo está
abierto.

La forma relativa además envejece bien: con una línea base del 1% —un libro de ventas real— los
mismos multiplicadores reproducen los umbrales clásicos. El umbral no cambia de valor; cambia de
naturaleza.

**Trade-offs:**
- (+) Los flags vuelven a discriminar, y con ellos las ramas MEDIUM y LOW del enrutador
- (+) La línea base se consulta, no se fija: cargar más transacciones la mueve sola
- (+) `merchant_risk_profile` devuelve `cb_ratio_baseline` — un 0.75 sin su referencia no se puede leer
- (-) Un multiplicador es más difícil de explicarle a un analista que «más del 2%»
- (-) Sobre un corpus muy chico la línea base es ruidosa; con menos de ~30 transacciones habría
  que caer a un umbral absoluto, y hoy eso no está implementado

---

## 18. El enrutador mira `requires_hitl`, no el nivel de riesgo

**Decisión:** `Switch — Derivación` enruta por `resolution.requires_hitl`. La primera salida va al
`Wait`; el resto, al informe.

**Razonamiento:** Enrutaba por `risk_level == "HIGH"`. Pero el nivel de riesgo y la necesidad de
un analista son dos preguntas distintas que `_determine_outcome` responde por separado: un caso
con un solo FAIL y sin fraude severo sale `MEDIUM` **y** `requires_hitl: true`. Ese caso caía en
la rama MEDIUM, generaba el informe y el webhook respondía 200 — la API pidiendo una persona y la
orquestación cerrando el caso.

Además era lógica de decisión repartida entre n8n y Python, que es justamente lo que la consigna
prohíbe: el canvas estaba re-derivando «¿hace falta un humano?» a partir del nivel de riesgo, en
vez de leer el campo que ya contesta esa pregunta.

Las ramas por nivel de riesgo se conservan después de la primera: siguen documentando en el canvas
qué tan grave es cada caso, que es información útil para quien lee el circuito. Lo que ya no hacen
es decidir.

**Trade-offs:**
- (+) Un solo lugar decide si hace falta una persona, y es el mismo que lo calcula
- (+) El canvas sigue mostrando el nivel de riesgo sin usarlo para derivar
- (-) La rama HIGH dejó de ser la que frena, así que el nombre «Switch — Nivel de Riesgo» dejó de
  describirlo: hubo que renombrarlo, y eso rompe cualquier referencia externa al nodo por nombre

---

## 19. Lo que una política *hace* también es dato

**Decisión:** `puede_bloquear` y `sla_dias` son columnas de la tabla `policies`, expuestas en el
CRUD y presentes en la carga útil de Qdrant. `POLICY_SEED_*` en `constants.py` es sólo el valor
con el que arranca el dataset.

**Razonamiento:** «Las políticas son datos, no código» era cierto para el texto y falso para la
semántica. Dos cosas que la política *hace* vivían en `constants.py`:

- `BLOCKER_POLICY_CODES = {"POL-EXC-003"}` — se podía cargar una política nueva que dijera
  «rechazar automáticamente», indexarla, verla en el contexto del modelo… y nunca podía producir
  un rechazo. El veredicto se degradaba a FAIL en silencio.
- `SLA_STANDARD_DAYS = 10` — se podía editar la descripción de POL-SLA-002 y el modelo la leía
  cambiada, mientras `check_sla` seguía devolviendo diez días.

El corte quedó donde tiene sentido: **qué política aplica es una regla** —depende del cliente y
del país, y las reglas son código—; **cuánto concede esa política es un dato suyo**. Lo mismo con
bloquear: que exista una lista blanca sigue siendo una decisión de diseño, pero quién está en ella
es del documento.

`puede_bloquear` arranca en `false` por defecto y a propósito. Un BLOCKER frena un caso sin
revisión humana; habilitarlo tiene que ser un acto explícito, no el resultado de olvidarse un
campo. El guardrail contra la sobre-escalada del modelo no se pierde: se vuelve editable.

**Trade-offs:**
- (+) Una política nueva puede bloquear, y un plazo se cambia, sin deploy — verificado extremo a extremo
- (+) La semántica viaja con el documento a Qdrant: el guardrail no necesita volver a SQLite por veredicto
- (-) Un índice armado antes de que las columnas existieran no trae los campos; hay un respaldo a
  la semilla, pero lo correcto es reindexar
- (-) Alguien con acceso al CRUD puede habilitar una política bloqueante. Es exactamente el poder
  que la decisión concede, y por eso el `false` por defecto y la autenticación existen

---

## 20. El Juez califica al modelo, no a la corrección

**Decisión:** `policy_consistency` y `risk_assessment` se evalúan sobre la propuesta original del
modelo, capturada antes del override. Los otros tres criterios, sobre la resolución entregada.

**Razonamiento:** El Juez recibía la resolución ya corregida. Pero la acción, el nivel de riesgo y
la derivación los fija el código *siempre*: preguntarle a un modelo si esos campos son coherentes
con los veredictos es preguntarle si el override funcionó. **Dos de los cinco criterios no podían
bajar de 10 por construcción**, y arrastraban el promedio hacia arriba sin medir nada.

La propuesta original ya se capturaba —`_detect_divergence` la necesita para registrar las
contradicciones— así que el dato estaba; sólo no llegaba al Juez. Ahora viaja en
`_propuesta_del_modelo`, se le pasa al prompt en su propia sección, y se quita de la resolución
que el Juez ve para que no aparezca dos veces.

Que el override haya corregido un `APPROVE` sobre un BLOCKER no redime al modelo: el objetivo del
Juez es medir la calidad del razonamiento, y esa medición sólo tiene sentido sobre lo que el
razonamiento produjo.

**Trade-offs:**
- (+) Los cinco criterios miden algo que puede fallar
- (+) El score deja de estar inflado por construcción
- (-) **Bajará**, y no sé cuánto: medirlo requiere correr el modelo, y eso cuesta saldo. El badge
  sigue mostrando 8.7, que es el promedio de los informes guardados —generados con el Juez v2.0—,
  y está declarado como tal
- (-) Un prompt más largo y con una sección condicional: si falta la propuesta, el Juez cae al
  comportamiento anterior y lo anota en `weaknesses`

---

## 21. El modelo de cada paso es configuración, no código

**Decisión:** Los tres pasos del pipeline —evaluación de políticas, síntesis y juez— eligen su
proveedor y su modelo por separado. La elección vive en la tabla `configuracion_modelos` de
SQLite, el panel la edita en caliente, y `constants.py` guarda el default. **Las claves no se
guardan nunca.**

**Razonamiento:** Ya había dos modelos —Haiku para evaluar, Sonnet para sintetizar y juzgar—,
pero la asignación estaba en `.env` y cambiarla era un redeploy. Y estaba mal repartida: el juez
compartía cliente con la síntesis porque `llm_resolution` servía para los dos, no porque alguien
hubiera decidido que evaluar y resolver piden lo mismo.

Son tres tareas distintas. Comparar datos contra reglas es mecánico. Redactar un análisis conectando
precedentes pide razonamiento. Aplicar una rúbrica de cinco criterios es una tercera cosa —y
posiblemente la que más conviene separar del modelo que escribió lo que se juzga—. Que cada una
pueda elegir su modelo es lo que permite responder empíricamente en vez de por intuición.

**El motivo inmediato fue más terrenal:** Anthropic no tiene free tier, y sin crédito el sistema no
se puede medir. Groq, Gemini, OpenRouter, Cerebras y GitHub Models sí lo tienen y todos hablan el
protocolo de OpenAI, así que `OpenAICompatibleClient` —una implementación más del `Protocol`, sin
tocar un solo llamador— abre esa puerta. De paso demuestra algo que `architecture.md` afirmaba sin
evidencia: que cambiar de proveedor es implementar el Protocol.

**Las claves son la parte que importa.** Se elige *qué* modelo, nunca *con qué credencial*: el
endpoint no acepta claves y hay un test que lo fija. Viajan por petición —el campo del panel— o
salen del entorno. Una instancia pública sin autenticación que persistiera claves ajenas sería un
incidente esperando.

**Trade-offs:**
- (+) Probar «¿y si el juez corre en otro modelo?» es un desplegable y un botón, no un deploy
- (+) El sistema se puede medir entero sin pagar, con un proveedor de free tier
- (+) El `Protocol` de `LLMClient` deja de ser una afirmación y pasa a tener dos implementaciones
- (-) **Un score medido con otro proveedor no es el score del sistema entregado.** Los prompts
  están afinados para Claude; medir con Llama mide otra cosa. Está dicho en el panel, en el
  `.env.example` y en el harness
- (-) Tres proveedores distintos a la vez no funcionan con una sola clave BYOK. El panel lo avisa
  y ofrece «usar en los tres pasos»
- (-) Una tabla más y un servicio más para algo que antes eran dos variables de entorno
