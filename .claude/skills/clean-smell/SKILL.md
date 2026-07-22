---
name: clean-smell
description: Detecta code smells específicos de Python/FastAPI/LLM en un archivo o en los archivos modificados recientemente.
argument-hint: "[archivo:línea — sin argumento usa git diff HEAD]"
---

# Skill: clean-smell

Detectá code smells concretos en el código indicado.

## Scope

**Con argumento** (`$ARGUMENTS`): analizá ese archivo específico (o `archivo:línea` para contexto puntual).

**Sin argumento**: ejecutá:
```
!`git diff HEAD --name-only`
```
y analizá los archivos modificados.

## Catálogo de smells a detectar

### Long Method
- Función/método > 30 líneas efectivas (sin contar docstring ni blank lines)
- En este proyecto: `services/resolution.py::resolve()` ya está en el límite
- Fix: extraer en métodos privados con nombres descriptivos

### Large Class
- Clase > 300 líneas o con > 8 métodos públicos
- Fix: separar responsabilidades en clases colaboradoras

### God Object
- Clase que importa y usa > 5 módulos distintos + tiene > 10 atributos
- En este proyecto: revisar si `dependencies.py` lifespan se está convirtiendo en uno
- Fix: extraer sub-factories

### Feature Envy
- Método que accede más a datos de otra clase que a los propios
- Síntoma: `other.x`, `other.y`, `other.z` en una función que vive en `Self`
- Fix: mover el método a la clase que más accede

### Data Clumps
- El mismo grupo de 3+ parámetros aparece en más de un método
- En este proyecto: `(tx_data, policies, similar_cases, logs, merchant_risk, client_history, motivo, cliente_vip)` — candidato
- Fix: agrupar en un `ResolveInput` dataclass o Pydantic model

### Primitive Obsession
- Usar `str` para un concepto de dominio (IDs, códigos de política, estados)
- Usar `dict` donde debería haber un modelo tipado
- Fix: crear NewType, StrEnum, o Pydantic model

### Magic Numbers / Magic Strings
- Literal numérico que no está en `domain/constants.py`
- Literal string que debería ser un enum (ej: `"BLOCKER"`, `"APPROVE"`, `"ERROR"`)
- En este proyecto: verificar que no hayan escapado al refactoring de Fase 3

### Dead Code
- Funciones definidas pero nunca llamadas
- Variables asignadas y nunca leídas
- Ramas `if False:` o condiciones siempre verdaderas
- Archivos importados en `__init__.py` que no usa ninguna ruta
- En este proyecto: `v1_log_analysis.py` sospechoso

### Duplicate Code
- Lógica idéntica o casi idéntica en 2+ lugares
- Template de formateo de datos → debería estar en `formatter.py`
- Conteo de severidades → centralizado en `Analyzer.count_severities()` pero verificar

### Silent Failures
- `except Exception: pass`
- `except:` bare
- `except Exception as e:` sin ningún `log`, `raise`, ni `return` con el error
- En este proyecto: `retriever.py` y `tracer.py` tienen varios

### Missing Guard Clauses
- Función que empieza con `if condition: ... else: [todo el cuerpo]`
- Preferable: `if not condition: return early`
- Reduce anidamiento y hace el happy path más legible

### Inappropriate Intimacy
- Módulo A importa internals de módulo B que no deberían ser públicos
- Variables `_private` accedidas desde fuera de la clase

## Formato de output

```
## SMELL REPORT — {{archivo o "git diff"}}

### {{Nombre del smell}} — {{severidad}}
**Ubicación:** `archivo.py:línea`
**Evidencia:**
```python
# código problemático (2-5 líneas de contexto)
```
**Por qué es un smell:** ...
**Refactor sugerido:** ...

---
```

Si hay más de 10 smells, agrupar por tipo y dar el top 5 más impactantes primero.
