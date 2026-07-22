---
name: clean-solid
description: Analiza los 5 principios SOLID en el codebase y emite una nota A/B/C/D/F por principio con evidencia concreta.
argument-hint: "[path/opcional — sin argumento analiza api/app/ completo]"
---

# Skill: clean-solid

Analizá el cumplimiento de los principios SOLID en este proyecto FastAPI/Python.

## Scope

Si se pasa `$ARGUMENTS`, limitar al path indicado. Sin argumento: `api/app/`.

## Análisis por principio

Para cada principio, leé los archivos relevantes y evaluá con evidencia real (archivo:línea).

---

### S — Single Responsibility Principle

**Pregunta:** ¿Cada clase/módulo tiene una sola razón para cambiar?

**Qué buscar en este proyecto:**
- `db.py` — ¿sigue haciendo solo data access o tiene lógica de negocio? (Fase 5 lo limpió)
- `analyzer.py` — ¿tiene más de una responsabilidad? (business logic + SLA + patterns)
- `dependencies.py` — el lifespan es largo, ¿es un smell?
- Rutas que hacen más que delegar al service
- Services que mezclan orquestación con lógica de negocio

**Señales de violación:**
- Un módulo que importa desde capas muy distintas (domain + rag + llm + data a la vez)
- Clases con métodos que operan sobre datos completamente distintos
- Comentarios `# Part 1: ... # Part 2: ...` dentro de una sola función

---

### O — Open/Closed Principle

**Pregunta:** ¿El código está abierto para extensión pero cerrado para modificación?

**Qué buscar:**
- Cadenas `if payment_method == "Cripto": ... elif payment_method == "Debito": ...`
- Lógica en `QueryBuilder.for_policies()` que hardcodea casos especiales (IVR, Cripto, LATAM)
- `_validate_resolution()` — ¿agregar una nueva guardrail requiere modificar el método?
- Prompts que hardcodean comportamientos que deberían ser configurables
- Condiciones hardcodeadas por país, canal o método de pago

**Señales de violación:**
- Agregar un nuevo tipo de transacción requiere modificar N funciones existentes
- `if country == "USA"` en lógica de negocio en vez de una tabla de configuración

---

### L — Liskov Substitution Principle

**Pregunta:** ¿Los subtipos son sustituibles por sus tipos base sin alterar el comportamiento?

**Qué buscar:**
- `LLMClient` Protocol — ¿`AnthropicClient` cumple el contrato completamente?
- `Tracer` Protocol — ¿`LangfuseTracer` y `NoOpTracer` son intercambiables sin sorpresas?
- `MockLLMClient` en tests — ¿se comporta como `LLMClient` en todos los aspectos?
- ¿Algún método de Protocol tiene `...` como body pero la implementación lanza `NotImplementedError`?
- ¿Alguna implementación amplía precondiciones o reduce postcondiciones del Protocol?

**Señales de violación:**
- Implementación lanza excepciones que el Protocol no declara
- Tests que `isinstance()` el cliente para comportarse diferente (violaría LSP)
- Implementaciones que ignoran parámetros definidos en el Protocol

---

### I — Interface Segregation Principle

**Pregunta:** ¿Las interfaces son lo más pequeñas posible? ¿Los clientes dependen solo de lo que usan?

**Qué buscar:**
- `LLMClient` Protocol — ¿solo tiene `complete()`? ¿Es suficiente o muy fat?
- `Tracer` Protocol — ¿todos los métodos (`trace`, `generation`, `score`) los usan todos los clientes?
- `ResolveRequest` — ¿tiene más campos de los que una ruta específica necesita?
- `FeedbackRequest` — ¿idem?
- ¿Hay clases que importan un módulo entero solo para usar una función?

**Señales de violación:**
- `NoOpTracer` tiene que implementar métodos que nunca hace nada — sugiere que el Protocol es demasiado amplio
- Clases que reciben objetos grandes solo para usar 1-2 atributos

---

### D — Dependency Inversion Principle

**Pregunta:** ¿Los módulos de alto nivel dependen de abstracciones, no de implementaciones concretas?

**Qué buscar:**
- `ResolutionService.__init__(self, llm, tracer)` — ¿`llm` está tipado como `LLMClient` (Protocol) o como `AnthropicClient`?
- `FeedbackService.__init__(self, db, updater, tracer)` — ¿`db` está tipado como `Database` (concreta) o existe un `DatabaseProtocol`?
- `Analyzer.__init__(self, db)` — idem
- `dependencies.py` — ¿crea instancias concretas o usa factories?
- `from ..llm.client import AnthropicClient` dentro de un service = viola DIP
- `from ..data.db import Database` en service = borderline (falta abstracción)

**Señales de violación:**
- `new ConcreteClass()` dentro de un service (debería inyectarse)
- Import de implementación concreta en capa de dominio o servicio
- No hay Protocol/ABC para `Database` — services dependen de concreta

---

## Formato de reporte

```
## SOLID REPORT — CIRI Chargeback Agent

### S — Single Responsibility: [nota A/B/C/D/F]
**Evidencia a favor:**
- ...

**Violaciones encontradas:**
| Archivo:Línea | Violación | Fix sugerido |
|---|---|---|

**Veredicto:** ...

---

### O — Open/Closed: [nota]
[mismo formato]

### L — Liskov Substitution: [nota]
[mismo formato]

### I — Interface Segregation: [nota]
[mismo formato]

### D — Dependency Inversion: [nota]
[mismo formato]

---

## Resumen SOLID
| Principio | Nota | Issues | Prioridad |
|---|---|---|---|
| S | | | |
| O | | | |
| L | | | |
| I | | | |
| D | | | |

## Recomendación principal (1 cambio de mayor impacto)
...
```

## Escala de notas

| Nota | Criterio |
|------|---------|
| A | Sin violaciones encontradas, bien aplicado |
| B | 1-2 violaciones menores, patrón correcto |
| C | Violaciones notables que aumentan el riesgo de cambio |
| D | Violaciones sistemáticas, el principio no se aplica |
| F | Violaciones críticas que generan bugs o bloquean extensiones |
