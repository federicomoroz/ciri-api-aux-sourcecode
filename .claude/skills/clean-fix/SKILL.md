---
name: clean-fix
description: Aplica el fix para un issue específico de clean code. Recibe archivo:línea o ID de deuda (D1-D12). Explica el principio, aplica el cambio, sugiere tests.
argument-hint: "<archivo:línea | D1..D12> [descripción opcional]"
---

# Skill: clean-fix

Aplicá el fix para un issue concreto de clean code.

## Input

`$ARGUMENTS` puede ser:
- `archivo.py:línea` — fix puntual en esa ubicación
- `D1`, `D2`, ... `D12` — fix de un item de deuda conocido (ver MEMORY.md)
- Descripción libre: "agrega response model al endpoint /resolve"

## Proceso

### 1. Entender el contexto

- Leé el archivo afectado (contexto completo de la función/clase, no solo la línea)
- Si es un item Dx, consultá `.claude/agents/clean-code-expert/MEMORY.md` para el detalle
- Identificá el principio violado (SOLID, DRY, error handling, type safety, etc.)

### 2. Explicar antes de cambiar

Antes de tocar el código, mostrá:
```
## Fix Plan: {{descripción del issue}}

**Principio violado:** {{ej: SRP, silent failure, missing type}}
**Archivo/línea:** {{ruta:línea}}
**Impacto de no resolver:** {{qué puede salir mal}}

**Cambio propuesto:**
- Antes: {{descripción de lo que hay}}
- Después: {{descripción de lo que habrá}}

**Archivos a modificar:** {{lista}}
**Tests a agregar/modificar:** {{lista}}
**Breaking change:** {{sí/no y por qué}}
```

### 3. Aplicar el fix

Usá las herramientas de edición para aplicar el cambio.

**Principios para aplicar el fix:**
- Mínimo cambio necesario — no refactorizar lo que no se pidió
- Mantener el estilo existente (snake_case, comillas dobles/simples, etc.)
- No agregar docstrings donde no había si no es el objetivo
- No agregar features adicionales
- Respetar el patrón de importación existente (orden: stdlib → third-party → local)

### 4. Verificar

Ejecutá los tests para confirmar que no se rompió nada:
```
!`cd /d/Proyectos/getJob/quest_ML && python -m pytest tests/ --tb=short -q 2>&1 | tail -10`
```

Reportá:
- ✅ N passed — fix exitoso
- ❌ Error — describí qué falló y proponé solución

### 5. Sugerir tests adicionales

Si el fix resuelve un caso no cubierto por tests existentes, proponé (no creés a menos que se pida):
```python
# tests/unit/test_{{módulo}}.py

def test_{{descripción_del_caso}}():
    """{{Descripción del comportamiento esperado}}."""
    # Arrange
    ...
    # Act
    ...
    # Assert
    ...
```

### 6. Actualizar MEMORY (si corresponde)

Si el fix resuelve un item Dx de la MEMORY, marcalo como resuelto en `.claude/agents/clean-code-expert/MEMORY.md`.

---

## Fixes comunes en este proyecto

### Silent exception → logging + específico
```python
# ANTES
except Exception:
    pass

# DESPUÉS
import logging
logger = logging.getLogger(__name__)

except (QdrantException, ConnectionError) as e:
    logger.warning(f"Cache check failed, skipping: {e}")
```

### `-> dict` → response model
```python
# ANTES
@router.post("/resolve")
def resolve(...) -> dict:
    return service.resolve(...)

# DESPUÉS (crea el model primero)
class ResolutionResponse(BaseModel):
    recommended_action: ResolutionOutcome
    confidence: float
    # ... campos del dict actual
    guardrail_warnings: list[str]

@router.post("/resolve", status_code=200)
def resolve(...) -> ResolutionResponse:
    result = service.resolve(...)
    return ResolutionResponse(**result)
```

### Missing Field constraints
```python
# ANTES
class Transaction(BaseModel):
    fraud_score: int
    amount_usd: float

# DESPUÉS
from pydantic import Field

class Transaction(BaseModel):
    fraud_score: int = Field(..., ge=0, le=100)
    amount_usd: float = Field(..., gt=0)
```

### LLM client sin exception handling
```python
# ANTES
response = self.client.messages.create(...)

# DESPUÉS
import anthropic

try:
    response = self.client.messages.create(...)
except anthropic.AuthenticationError as e:
    raise ValueError(f"Anthropic API key inválida: {e}") from e
except anthropic.RateLimitError as e:
    raise RuntimeError(f"Rate limit Anthropic, reintentar: {e}") from e
except anthropic.APIError as e:
    logger.error(f"Error de API Anthropic: {e}")
    raise
```

### Tipo `db` sin anotar
```python
# ANTES
def __init__(self, indexer: QdrantIndexer, db, threshold: float):

# DESPUÉS
from ..data.db import Database

def __init__(self, indexer: QdrantIndexer, db: Database, threshold: float):
```
