# Clean Code Expert — CIRI Chargeback Agent

## Proyecto
**CIRI Chargeback Agent** — FastAPI + Pydantic v2 + Qdrant (RAG) + Anthropic Claude + SQLite.
Stack Python 3.12+. Tests con pytest (50/50). Sin framework ORM, SQLite raw con parámetros.

---

## Arquitectura (post-refactoring 9 fases — jul-2026)

```
api/app/
├── domain/
│   ├── constants.py      ← 27+ magic numbers centralizados
│   ├── enums.py          ← LATAM_COUNTRIES canónico + StrEnums
│   └── models.py         ← Pydantic v2, enums conectados
├── config.py             ← Settings (pydantic-settings)
├── data/
│   ├── db.py             ← SOLO data access, sin business logic
│   └── loader.py         ← init SQLite + carga Excel
├── services/             ← Capa de servicio (orquestación)
│   ├── resolution.py     ← ResolutionService (resolve + judge + guardrails)
│   └── feedback.py       ← FeedbackService (feedback + auto-indexing)
├── analysis/
│   └── analyzer.py       ← TODA business logic: flags, SLA, patterns, count_severities()
├── rag/
│   ├── formatter.py      ← format_policies/cases_for_prompt
│   ├── retriever.py      ← QdrantRetriever + QueryBuilder
│   ├── indexer.py        ← QdrantIndexer
│   └── updater.py        ← RAGUpdater (auto-indexing de feedback)
├── llm/
│   ├── client.py         ← LLMClient Protocol + AnthropicClient
│   ├── parsing.py        ← parse_json_safely
│   └── prompts/          ← v1_policy_eval, v1_resolution, v1_judge
├── observability/
│   └── tracer.py         ← Tracer Protocol + LangfuseTracer + NoOpTracer
├── reports/
│   └── generator.py      ← HTML report generation
├── routes/               ← Thin handlers (~20 líneas max)
└── dependencies.py       ← DI via FastAPI lifespan + app.state
```

---

## Deuda Técnica Identificada (auditoría jul-2026)

### CRITICAL

**D1 — Silent exception handling (bare `except Exception: pass`)** [ABIERTA]
- `rag/retriever.py:149-150` — semantic cache check silencia cualquier error
- `rag/retriever.py:166-167` — store_in_cache silencia fallos de escritura
- `observability/tracer.py` — múltiples `except Exception: pass`
- `dependencies.py:87-89, 95-97` — inicialización de colecciones Qdrant
- Fix: Usar excepciones específicas (QdrantError, etc.) + `logger.warning(f"...: {e}")`

**D2 — Todas las rutas devuelven `-> dict` en vez de response models** [ABIERTA]
- Afecta: analyze.py, feedback.py, logs.py, merchants.py, cases.py, sla.py, transactions.py, health.py
- Excepción: policies.py tiene partial typing
- Fix: Crear `PolicyResponse`, `ResolutionResponse`, `JudgeResponse`, `FeedbackResponse`, etc.

### HIGH

**D3 — LLM client sin manejo de excepciones de API** [ABIERTA]
- `llm/client.py:37` — `self.client.messages.create(...)` sin try/except
- No maneja: AuthenticationError, RateLimitError, APIError, timeout
- Fix: Envolver en try/except con excepciones específicas de anthropic

**D4 — Qdrant init usa `except Exception` demasiado amplio** [ABIERTA]
- `dependencies.py:83-97` — no distingue "collection no existe" de "error de red"
- Fix: `except ResponseHandlingException` para el caso esperado + log para el resto

**D5 — Ausencia de logging en la service layer** [ABIERTA]
- `services/resolution.py`, `services/feedback.py`, `analysis/analyzer.py`
- No hay trazabilidad de qué ocurre entre el inicio y fin de cada request
- Fix: `logging.getLogger(__name__)` + log en puntos clave

### MEDIUM

**D6 — Sin validators de dominio en modelos Pydantic** [ABIERTA]
- `Transaction.fraud_score: int` — puede ser negativo o > 100
- `Transaction.amount_usd: float` — puede ser negativo
- `Resolution.confidence: float` — puede salirse de [0.0, 1.0]
- `Resolution.compensation_amount_usd: float` — puede ser negativo o > 15
- Fix: `Field(ge=0, le=100)` + `@field_validator` para reglas compuestas

**D7 — Sin status codes explícitos en la mayoría de rutas** [ABIERTA]
- `@router.post("/resolve")` sin `status_code=200`
- Solo policies.py usa `status_code=201` correctamente
- Fix: Agregar `status_code=` en todos los decoradores

**D8 — `v1_log_analysis.py` es dead code** [ABIERTA]
- Archivo existe en `llm/prompts/` pero no está importado ni usado
- Fix: Conectar a un endpoint o eliminar

**D9 — Fechas inconsistentes** [ABIERTA]
- `Policy` usa `datetime` con timezone
- `Transaction`, `HistoricalCase` usan strings
- `analyzer.py` parsea a `date` (pierde info de hora)
- Fix: Estandarizar a `datetime` con UTC en toda la capa de dominio

**D10 — `RAGUpdater.__init__` tiene `db` sin tipo** [ABIERTA]
- `def __init__(self, indexer: QdrantIndexer, db, ...)` — `db` debería ser `db: Database`
- Fix: Agregar import y anotación

### LOW

**D11 — Gaps de test coverage** [ABIERTA]
- Sin tests para guardrails (APPROVE+BLOCKER auto-correct, compensation ratio, confidence)
- Sin tests para validación de modelos (fraud_score < 0, confidence > 1)
- Sin tests para scenarios de error (LLM timeout, Qdrant caído)
- Fix: `tests/unit/test_guardrails.py`, `tests/unit/test_model_validation.py`

**D12 — Formato de errores inconsistente en health check** [ABIERTA]
- `health.py` devuelve `sqlite_status = f"error: {e}"` como string, no como dict estructurado
- Fix: `{"status": "error", "message": str(e)}`

---

## Convenciones del Proyecto

- **Magic numbers**: Siempre en `domain/constants.py`
- **LATAM_COUNTRIES**: Canónico en `domain/enums.py`, importado desde `constants.py`
- **Business logic**: Solo en `analysis/analyzer.py` o `services/`
- **Data access**: Solo en `data/db.py`
- **Thin routes**: Max ~20 líneas, delegan a services
- **Enums en models**: Usar el StrEnum del dominio, NO string literal
- **Imports**: stdlib → third-party → local, sin wildcards
- **datetime**: `datetime.now(timezone.utc)` (NO `datetime.utcnow()`)
- **Tests**: Cada fase de refactoring debe mantener 50/50

---

## Heurísticas de Severidad para Este Proyecto

| Severidad | Criterio |
|-----------|---------|
| CRITICAL | Silencia errores (\`pass\` en except), rompe contrato de API, riesgo de datos incorrectos |
| HIGH | Sin manejo de errores en I/O externo (LLM API, Qdrant), sin tipos en bordes del sistema |
| MEDIUM | Falta validación de dominio, dead code, logging ausente, fechas inconsistentes |
| LOW | Docstrings faltantes, status codes no explícitos, tests de edge cases |

---

## Historial de Refactoring

| Fase | Descripción | Estado |
|------|-------------|--------|
| 0 | Dead code + deprecation fixes (utcnow) | ✅ Completa |
| 1 | `domain/constants.py` — magic numbers | ✅ Completa |
| 2 | Enums conectados a models | ✅ Completa |
| 3 | Magic numbers reemplazados en 8 archivos | ✅ Completa |
| 4 | `rag/formatter.py` + `llm/parsing.py` extraídos | ✅ Completa |
| 5 | Business logic de db.py → analyzer.py | ✅ Completa |
| 6 | Service layer creada, routes thin | ✅ Completa |
| 7 | `Analyzer.count_severities()` centralizado | ✅ Completa |
| 8 | MockLLMClient → tests/conftest.py | ✅ Completa |
| 9 | Type annotations + CORS restringido | ✅ Completa |
