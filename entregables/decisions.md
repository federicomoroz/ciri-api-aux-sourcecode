# Decisiones Técnicas — CIRI Chargeback Agent

Este documento explica el **por qué** detrás de cada decisión técnica importante. Formato: Contexto → Decisión → Razonamiento → Trade-offs → Qué haría distinto en producción.

Una nota sobre el stack: este proyecto fue construido con las restricciones reales de un free tier: n8n Cloud (trial), Render (free tier con cold starts de ~50s), Qdrant Cloud (1GB free), Voyage AI (free tier). Cada decisión refleja ese contexto — no tuve acceso a infraestructura dedicada, pero el diseño está pensado para escalar cuando lo haya.

---

## 1. Orquestación explícita con n8n (no AI Agent)

**Contexto:** El sistema necesita un orquestador para las investigaciones de contracargos. n8n tiene un nodo "AI Agent" que le da al LLM control sobre qué tools llamar y en qué orden. La alternativa es orquestación explícita con nodos nombrados.

**Decisión:** Usar 54 nodos explícitos en n8n (43 ejecutables + 11 sticky notes) con HTTP Request, Set, Switch, Wait. Sin nodo AI Agent, sin tool-calling del LLM en el workflow.

**Razonamiento:** En una fintech, un regulador o un oficial de compliance necesita ver exactamente qué pasó, en qué orden, para cada caso. Un AI Agent es una caja negra — el LLM decide el flujo en runtime, lo que lo hace no-determinístico e imposible de auditar. Con nodos explícitos, cada investigación sigue los mismos pasos en el mismo orden: 7 llamadas de contexto → evaluación de políticas → síntesis de resolución → Judge → ruteo por riesgo. El flujo queda visible en el canvas de n8n, versionado como JSON, y es reproducible.

Elegí n8n porque CIRI lo usa como herramienta diaria. Traté de usar la mayor cantidad de nodos nativos posible (Switch, Wait, Set, IF, Code) en lugar de depender solo de HTTP Requests.

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
- **Routes** (~20 líneas cada una) — solo HTTP: parsear request, llamar servicio, devolver response
- **Services** (`ResolutionService`, `FeedbackService`) — orquestan múltiples pasos (llamadas LLM, guardrails, caching)
- **Analyzer** (`analysis/analyzer.py`) — lógica de negocio pura: reglas SLA, flags de riesgo, patrones de error

El acceso a datos está aislado en `data/db.py`. Las definiciones de dominio (models, enums, constants) tienen cero dependencias externas.

**Razonamiento:** Esto hace que cada capa sea testeable independientemente. Los tests unitarios mockean solo la capa de abajo. Las rutas se testean con `TestClient` y servicios mock. Los servicios se testean con clientes LLM mock. El analyzer son funciones puras — sin mocks.

Con 244 tests pasando, esta arquitectura demostró ser robusta para iterar rápido sin romper cosas.

**Trade-offs:**
- (+) Cada capa tiene una sola responsabilidad
- (+) Tests unitarios son rápidos y enfocados
- (+) Fácil de intercambiar implementaciones (ej: distinto proveedor LLM)
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

## 9. Caché semántico en Qdrant

**Contexto:** Casos de contracargo similares (mismo comercio, mismo método de pago, montos similares) frecuentemente producen resoluciones LLM idénticas. Llamar al LLM para cada caso es caro y lento.

**Decisión:** Usar una colección `_semantic_cache` en Qdrant. Antes de llamar al LLM para resolución, embedear el query y buscar en el cache (threshold 0.92). Si hay hit, devolver la resolución cacheada inmediatamente — sin llamada LLM.

**Razonamiento:** Con Sonnet para synthesis y Judge, el costo por investigación no es trivial. El umbral de 0.92 balancea tasa de hit contra precisión. A 0.92, solo casos casi idénticos matchean — mismo tipo de comercio, mismo método de pago, montos similares. Esto es seguro porque las políticas que aplican a estos casos son las mismas. El cache usa la misma instancia de Qdrant que ya está corriendo para RAG, así que no hay infraestructura adicional.

**Trade-offs:**
- (+) Reduce costos LLM ~20% para patrones recurrentes de comercio
- (+) Respuesta sub-100ms para cache hits vs 3-5s para llamadas LLM
- (+) Sin infraestructura adicional — reutiliza Qdrant existente
- (-) Invalidación de cache es implícita — nuevas políticas no invalidan automáticamente resoluciones cacheadas
- (-) Umbral 0.92 puede ser muy agresivo o muy conservador según el landscape de políticas

**En producción:** Invalidación explícita de cache cuando se actualizan políticas. TTL (ej: 7 días) para que resoluciones cacheadas no persistan indefinidamente. Métricas de hit/miss en Langfuse.

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
