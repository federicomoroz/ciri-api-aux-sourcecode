---
name: clean-audit
description: Auditoría completa de clean code para el proyecto CIRI. Lanza 3 agentes en paralelo y consolida un informe CRITICAL/HIGH/MEDIUM/LOW con archivo:línea.
argument-hint: "[path/opcional — sin argumento audita todo api/]"
---

# Skill: clean-audit

Sos el agente de calidad de este proyecto. Ejecutá una auditoría técnica completa del codebase.

## Scope

Si se pasa `$ARGUMENTS`, limitar al path indicado. Sin argumento: auditar `api/app/` completo.

## Qué detectar

Lanzá 3 agentes en paralelo con el Agent tool, cada uno cubriendo una dimensión:

### Agente A — Estructura y tipos
- Funciones > 30 líneas (god functions)
- Clases > 300 líneas (god objects)
- `-> dict` / `-> list` como return type cuando debería ser un model Pydantic
- Parámetros tipados como `dict` cuando debería ser un TypedDict/BaseModel
- Missing type hints en parámetros o returns
- Imports `from x import *` (wildcard)
- Imports circulares
- Imports sin usar (después del refactoring pueden quedar)

### Agente B — Manejo de errores y observabilidad
- `except Exception: pass` o `except:` bare (silencian errores)
- Bloques try sin logging del error antes de continuar
- I/O externo sin try/except: Qdrant calls, Anthropic API calls, SQLite
- `time.time()` sin timeout guard en llamadas a APIs externas
- `except Exception as e:` que no loguea `e`
- Ausencia de `logging.getLogger(__name__)` en módulos de servicio/análisis

### Agente C — Diseño y dominio
- Business logic en rutas (más de delegación simple)
- Data access en services o routes (debería solo estar en db.py)
- Magic numbers (literales numéricos no en constants.py)
- Magic strings (literales de string que deberían ser constantes o enums)
- Validaciones de dominio faltantes en modelos Pydantic (Field constraints)
- Dead code: archivos importados en __init__ pero no usados en ninguna ruta
- Duplicación: lógica idéntica en más de un lugar
- Fechas como strings cuando deberían ser datetime con timezone

## Formato de reporte

```
## AUDIT REPORT — CIRI Chargeback Agent ({{fecha}})
Scope: {{path auditado}}

### CRITICAL
| Archivo:Línea | Issue | Principio violado | Fix en 1 línea |
|---|---|---|---|

### HIGH
| Archivo:Línea | Issue | Principio violado | Fix en 1 línea |
|---|---|---|---|

### MEDIUM
| Archivo:Línea | Issue | Principio violado | Fix en 1 línea |
|---|---|---|---|

### LOW
| Archivo:Línea | Issue | Principio violado | Fix en 1 línea |
|---|---|---|---|

## Resumen
| Dimensión | CRITICAL | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|---|
| Estructura/Tipos | | | | | |
| Errores/Observabilidad | | | | | |
| Diseño/Dominio | | | | | |
| **TOTAL** | | | | | |

## Top 3 acciones recomendadas (mayor impacto/menor esfuerzo)
1. ...
2. ...
3. ...
```

## Criterios de severidad

| Severidad | Criterio |
|-----------|---------|
| CRITICAL | Silencia errores (`pass`), rompe contrato de API, riesgo de datos incorrectos en dominio |
| HIGH | Sin manejo en I/O externo (LLM, Qdrant), tipos faltantes en frontera del sistema |
| MEDIUM | Validación de dominio ausente, dead code, logging ausente, inconsistencias |
| LOW | Docstrings faltantes, status codes, edge cases sin tests |

## Referencia de deuda conocida

Consultá `.claude/agents/clean-code-expert/MEMORY.md` para la lista de deuda ya identificada (D1-D12). Marcá en el reporte si un hallazgo es **nuevo** o **ya conocido**.
