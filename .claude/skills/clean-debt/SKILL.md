---
name: clean-debt
description: Muestra el estado de la deuda técnica del proyecto o la actualiza después de un refactoring.
argument-hint: "[update — sin argumento muestra deuda actual; con 'update' actualiza MEMORY del agente]"
---

# Skill: clean-debt

Gestioná la deuda técnica del proyecto CIRI Chargeback Agent.

## Modos de operación

### Modo visualización (sin argumento)

Leé `.claude/agents/clean-code-expert/MEMORY.md` y mostrá el estado actual de la deuda en formato dashboard:

```
## DEUDA TÉCNICA — CIRI Chargeback Agent
Última actualización: {{fecha de la última entrada en MEMORY.md}}

### Estado por severidad
| Severidad | Abiertas | Resueltas | Total |
|---|---|---|---|
| CRITICAL | | | |
| HIGH | | | |
| MEDIUM | | | |
| LOW | | | |

### Items abiertos
| ID | Descripción | Severidad | Archivo principal | Esfuerzo estimado |
|---|---|---|---|---|
| D1 | Silent exception handling | CRITICAL | retriever.py, tracer.py | 2h |
| D2 | Routes retornan `dict` en vez de response models | CRITICAL | routes/*.py | 4h |
...

### Progreso de refactoring
| Fase | Descripción | Estado |
|---|---|---|
| 0 | Dead code + deprecation | ✅ |
...

### Métrica de deuda
- Items totales identificados: N
- Items resueltos: M
- Ratio de deuda activa: N-M/N × 100%
- Tiempo estimado de resolución: Xh
```

---

### Modo actualización (`update`)

Si `$ARGUMENTS` contiene `update`, hacé lo siguiente:

1. Pedile al usuario que describa qué cambios hizo (o inferí de `git diff HEAD` ejecutando):
```
!`git diff HEAD --stat`
```

2. Identificá qué items de la MEMORY.md (D1-D12 y futuros) fueron resueltos o son nuevos hallazgos.

3. **Actualizá** `.claude/agents/clean-code-expert/MEMORY.md`:
   - Marcá los items resueltos con `[RESUELTA — fecha]`
   - Agregá nuevos items encontrados con `[NUEVA — fecha]`
   - Actualizá el historial de refactoring si se completó una nueva fase

4. Confirmá los cambios realizados en la MEMORY.

---

### Modo comparación (`compare`)

Si `$ARGUMENTS` contiene `compare`, mostrá la evolución de la deuda:
- Items que estaban abiertos en la sesión anterior y siguen abiertos
- Items resueltos recientemente (desde última actualización)
- Items nuevos introducidos

```
## EVOLUCIÓN DE DEUDA TÉCNICA

### Resueltas desde la última sesión ✅
- D3 — LLM client sin manejo de excepciones (resuelto jun-2026)

### Nuevas introducidas ⚠️
- D13 — Sin timeout en embedding model calls (nueva jul-2026)

### Sin cambios (N items abiertos)
- D1, D2, D5, D6...
```

---

## Criterios de esfuerzo estimado

| Esfuerzo | Descripción |
|----------|-------------|
| 30min | Cambio en una sola función, 1-5 líneas |
| 1h | Cambio en un módulo, posible test adicional |
| 2h | Cambio en varios módulos + tests |
| 4h | Cambio arquitectónico, requiere actualizar tests de integración |
| 1d | Refactoring mayor, múltiples capas afectadas |
