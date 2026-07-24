# Prompts — Agente de Contracargos CIRI

Los prompts se almacenan como modulos Python versionados en `api/app/llm/prompts/`. Cada modulo exporta `SYSTEM`, `USER_TEMPLATE` y una funcion `render()` que devuelve `(system_prompt, user_prompt)` como tupla.

**Excepcion — v1_judge:** El prompt del Juez se invoca directamente desde el nodo `[Juez de Calidad]` (HTTP Request) en n8n hacia `POST https://api.anthropic.com/v1/messages` usando `$env.CB_ANTHROPIC_API_KEY`. La respuesta se parsea con `JSON.parse($json.content[0].text)` en el nodo `[Extraer Evaluacion — Juez]`. El archivo Python `v1_judge.py` es la fuente canonica; el nodo de n8n lo replica. La ruta FastAPI `/api/analyze/judge` sigue disponible para testing directo.

---

## Principio central: "El codigo decide, el LLM explica"

De los 11 campos del JSON de resolucion, **6 son deterministas** — calculados por Python en `ResolutionService._determine_outcome()` sin intervencion del LLM:

| Campo | Origen | Metodo |
|---|---|---|
| `recommended_action` | Determinista | `_determine_outcome()` — logica de verdicts |
| `risk_level` | Determinista | `_determine_outcome()` — BLOCKER/FAIL counts + fraud_score |
| `risk_reason` | Determinista | `_determine_outcome()` — texto explicativo generado por codigo |
| `requires_hitl` | Determinista | `_determine_outcome()` — derivado de action |
| `precedent_summary` | Determinista | `_build_precedent_summary()` — patron matching + tendencias |
| `policy_verdicts` | LLM (Call 1) | Evaluados por v1_policy_eval, sanitizados por `_sanitize_verdicts()` |
| `justification` | LLM (Call 2) | Generado por v1_resolution (Sonnet) |
| `confidence` | LLM (Call 2) | Estimado por v1_resolution |
| `next_steps` | LLM (Call 2) | Generado por v1_resolution |
| `log_summary` | Determinista | `_summarize_logs()` — conteo de severidades + patrones |
| `compensation_*` | LLM (Call 2) | Evaluado por v1_resolution segun SLA |

El LLM recibe la decision ya tomada en la seccion `DECISION DETERMINADA` del prompt y su tarea es justificarla con evidencia, no tomarla. Esto elimina la categoria entera de errores donde el LLM elige una accion incorrecta (por ejemplo, APPROVE con BLOCKER activo).

### Logica determinista en detalle

```python
# Archivo: api/app/services/resolution.py — _determine_outcome()

BLOCKER en verdicts         → REJECT + risk BLOCKER
FAIL sin BLOCKER            → PENDING_HITL + risk HIGH o MEDIUM
requires_human_review=true  → PENDING_HITL (red de seguridad)
Solo PASS/WARNING           → APPROVE + risk LOW o MEDIUM
```

Ademas, `_sanitize_verdicts()` degrada cualquier BLOCKER emitido por el LLM para politicas fuera de `BLOCKER_POLICY_CODES` (actualmente solo `POL-EXC-003`) a FAIL + `requires_human_review=true`. Esto previene la sobre-escalacion del LLM (por ejemplo, asignar BLOCKER a un comercio suspendido, que no es tecnicamente irreversible).

---

## Estrategia dual-model

El pipeline utiliza dos modelos Claude para optimizar costo vs. calidad:

| Llamada | Modelo | Razon |
|---|---|---|
| Call 1: Evaluacion de politicas (v1.2) | **Haiku** | Tarea mecanica: comparar datos contra reglas. Haiku es rapido y suficiente. |
| Call 2: Sintesis de resolucion (v3.0) | **Sonnet** | Tarea analitica: razonar sobre precedentes, conectar evidencias, justificar. |
| Call 3: Juez de calidad (v2.0) | **Sonnet** | Tarea evaluativa: aplicar rubrica detallada, detectar inconsistencias. |

Configuracion en `.env`:
```
CB_LLM_MODEL=claude-haiku-4-5-20251001       # Call 1
CB_LLM_RESOLUTION_MODEL=claude-sonnet-4-20250514  # Call 2 + Call 3
```

---

## Evolucion del prompt engineering

### Iteracion de scores del Juez

| Fase | Score promedio | Cambio clave |
|---|---|---|
| v1.0 inicial | ~8.2 | 3 prompts basicos, todo con Haiku |
| v1.1 + precedentes | ~8.4 | Instrucciones de analisis de precedentes en v1_resolution |
| v1.2 policy_eval | ~8.6 | Logica de umbrales estricta, LATAM, documentacion — techo de Haiku identificado |
| v2.0 resolution | — | Campos deterministas sacados del LLM — pero Haiku aun justifica pobremente |
| v3.0 resolution + Sonnet | ~9.1 | Sonnet para Call 2 + Call 3, rubrica granular en Judge v2.0 |

**El techo de Haiku (8.6):** Al llegar a v1.2, la evaluacion de politicas era suficientemente precisa, pero la justificacion y el analisis de precedentes seguian siendo superficiales — Haiku listaba datos sin conectarlos analiticamente. La solucion no fue mejorar el prompt sino cambiar el modelo: Sonnet para las tareas que requieren razonamiento (Call 2 y Call 3) y mantener Haiku para la tarea mecanica (Call 1).

**De v2.0 a v3.0 — la transicion critica:** En v2.0, el prompt de resolucion pedia al LLM que extrajera datos mecanicamente (copiar campos, listar politicas). Esto era una tarea donde Haiku era suficiente pero el resultado carecia de profundidad analitica. En v3.0, al mover los 6 campos deterministas al codigo, el prompt se libero para pedir razonamiento genuino: "explica POR QUE este nivel de riesgo es adecuado", "RAZONA sobre las implicaciones de los precedentes", "conecta el patron de precedentes con la decision actual". Sonnet responde a estas instrucciones con analisis que Haiku no puede producir.

---

## Historial de versiones

| Prompt | Version | Fecha | Resumen |
|---|---|---|---|
| v1_policy_eval | v1.0 | 2025-01 | Version inicial — 5 veredictos, reglas Cripto=BLOCKER y FRD-001 |
| v1_policy_eval | v1.2 | 2025-07 | Logica matematica de umbrales (>, >=, <), determinacion LATAM, contexto de comercio/cliente, documentacion, ventanas temporales |
| v1_resolution | v1.0 | 2025-01 | Version inicial — 8 reglas estrictas, vocabulario de 4 acciones |
| v1_resolution | v2.0 | 2025-07 | Extraccion mecanica para Haiku — campos deterministas calculados externamente |
| v1_resolution | v3.0 | 2025-07 | Razonamiento analitico para Sonnet — "el codigo decide, el LLM explica" |
| v1_judge | v1.0 | 2025-01 | Version inicial — 5 criterios, APPROVE+BLOCKER = 1.0 automatico |
| v1_judge | v2.0 | 2025-07 | Rubrica granular por criterio (niveles 10.0, 9.0, 7.0-8.9, etc.), semantica de fraud_score, proteccion contra penalizacion incorrecta de PENDING_HITL |

---

## Prompt 1: v1_policy_eval (v1.2)

**Archivo:** `api/app/llm/prompts/v1_policy_eval.py`
**Modelo:** Haiku (Call 1)

### Proposito

Evaluar una transaccion contra cada politica recuperada por RAG y producir un veredicto estructurado para cada una. Es la primera llamada LLM del pipeline `/api/analyze/resolve` y determina que politicas estan violadas, cuales se cumplen y si existen bloqueos criticos.

### Rol

Auditor de cumplimiento de politicas para una fintech latinoamericana especializada en contracargos.

### Especificacion de entrada

| Parametro | Tipo | Descripcion |
|---|---|---|
| `transaction` | `dict` | Registro completo de la transaccion (id, amount, merchant, country, payment_method, fraud_score, channel, etc.) |
| `policies_text` | `str` | Lista de politicas formateada, recuperada de Qdrant via QueryBuilder |
| `policy_count` | `int` | Numero de politicas a evaluar |
| `merchant_risk` | `dict` | Perfil de riesgo del comercio (cb_ratio, flags, suspension) |
| `client_history` | `dict` | Historial del cliente (total_chargebacks, countries, flags) |

### Especificacion de salida

Array JSON de objetos `PolicyVerdict`:

```json
[
  {
    "policy_code": "POL-XXX-NNN",
    "verdict": "PASS | FAIL | BLOCKER | WARNING | NOT_APPLICABLE",
    "reasoning": "Explicacion concisa citando datos especificos de la transaccion",
    "requires_human_review": false
  }
]
```

**Definiciones de veredictos:**

| Veredicto | Significado |
|---|---|
| `PASS` | La transaccion cumple esta politica (la condicion de violacion NO se cumple) |
| `FAIL` | La transaccion viola esta politica (la condicion de violacion SI se cumple) |
| `BLOCKER` | Violacion critica — la transaccion es TECNICAMENTE IRREVERSIBLE (reservado para Cripto via POL-EXC-003). Un comercio suspendido o un cliente riesgoso NO son BLOCKER |
| `WARNING` | SOLO cuando falta un dato necesario para evaluar la condicion (ej: timestamps ausentes para verificar ventana temporal) |
| `NOT_APPLICABLE` | La politica genuinamente no aplica (ej: POL-EXC-002 VIP cuando el cliente no es VIP) |

### Reglas estrictas integradas en el prompt

1. **Umbrales — logica matematica estricta:**
   - "mas de 3" = >3 (si el valor es 3, la condicion NO se cumple → PASS)
   - "al menos 3" = >=3 (si el valor es 3, la condicion SI se cumple)
   - WARNING NO es para valores que no alcanzan el umbral — es SOLO para datos faltantes
   - Citar datos especificos: score=X, monto=USD Y, cb_count=N vs umbral=M, operador exacto
   - `total_chargebacks` del historial = conteo PREVIO (no incluir caso actual)
   - Ventanas temporales: si no hay timestamps, marcar WARNING (no FAIL)
2. `POL-EXC-003` aplica SIEMPRE como `BLOCKER` cuando el metodo de pago es "Cripto"
3. `POL-FRD-001` aplica como `FAIL` o `BLOCKER` cuando el score antifraude es inferior al umbral
4. Un `BLOCKER` significa que la resolucion final DEBE rechazar el contracargo
5. Evaluar TODAS las politicas proporcionadas — no omitir ninguna
6. Usar TODOS los datos disponibles: transaccion, perfil de riesgo del comercio e historial del cliente
7. `NOT_APPLICABLE` solo cuando la politica genuinamente no aplica; comercios suspendidos siguen siendo relevantes para politicas de plazos
8. Responder UNICAMENTE con un array JSON valido, sin texto adicional
9. **Determinacion LATAM:** Distinguir entre pais de la transaccion (campo `country`) y pais del comercio. Lista de paises LATAM explicita (MEX, COL, ARG, BRA, CHL, PER, etc.)
10. **Documentacion:** Si una politica requiere documentacion y se marca WARNING, especificar que documentos faltan y si bloquean la decision

### Ejemplo de prompt de usuario renderizado (abreviado)

```
## TRANSACCION
{
  "id": "TXN-00051",
  "merchant": "CryptoVault SA",
  "amount_usd": 850.00,
  "payment_method": "Cripto",
  "country": "ARG",
  "fraud_score": 8,
  "channel": "Web"
}

## PERFIL DE RIESGO DEL COMERCIO
{
  "merchant_name": "CryptoVault SA",
  "cb_ratio": 0.03,
  "flags": ["suspended_merchant"]
}

## HISTORIAL DEL CLIENTE
{
  "total_chargebacks": 1,
  "countries": ["ARG"],
  "flags": []
}

## POLITICAS A EVALUAR (recuperadas por RAG — 17 politicas)
**POL-EXC-003** — EXCEPCION
Nombre: Exclusion de criptomonedas
Descripcion: Las transacciones realizadas con criptomonedas son irreversibles...
Referencia: Reg. Fintech 2024/03

**POL-FRD-001** — FRAUDE
Nombre: Umbral antifraude
Descripcion: Transacciones con score < 15 requieren revision manual obligatoria...
...

Evalua cada politica usando TODOS los datos disponibles y devuelve el array JSON.
```

### Ejemplo de salida esperada

```json
[
  {
    "policy_code": "POL-EXC-003",
    "verdict": "BLOCKER",
    "reasoning": "Metodo de pago es Cripto (irreversible). BLOCKER automatico segun POL-EXC-003.",
    "requires_human_review": false
  },
  {
    "policy_code": "POL-FRD-001",
    "verdict": "BLOCKER",
    "reasoning": "Score antifraude = 8/100, significativamente inferior al umbral minimo. Alto riesgo de fraude confirmado.",
    "requires_human_review": false
  },
  {
    "policy_code": "POL-SLA-002",
    "verdict": "NOT_APPLICABLE",
    "reasoning": "La politica SLA de 10 dias habiles no es relevante dado que ya existe un BLOCKER que rechaza el caso.",
    "requires_human_review": false
  }
]
```

### Guardrail post-LLM: `_sanitize_verdicts()`

Despues de recibir los veredictos del LLM, el sistema aplica una sanitizacion determinista: cualquier veredicto `BLOCKER` para una politica fuera de `BLOCKER_POLICY_CODES` (actualmente solo `POL-EXC-003`) se degrada a `FAIL` con `requires_human_review=true`. Esto previene que Haiku sobre-escale situaciones que son graves pero no tecnicamente irreversibles.

### Registro de cambios

- **v1.0** (2025-01): Version inicial. Sistema de 5 veredictos. Reglas Cripto=BLOCKER y FRD-001 hardcoded.
- **v1.2** (2025-07): Logica matematica de umbrales con operadores explicitos (>, >=). Determinacion LATAM (pais de transaccion vs pais del comercio). Contexto enriquecido con perfil de riesgo del comercio e historial del cliente. Reglas de documentacion faltante. Ventanas temporales. Ejemplos de evaluacion correcta.

---

## Prompt 2: v1_resolution (v3.0)

**Archivo:** `api/app/llm/prompts/v1_resolution.py`
**Modelo:** Sonnet (Call 2)

### Proposito

Justificar y explicar una decision de contracargo que ya fue determinada por el sistema de guardrails. El LLM sintetiza la evidencia disponible — veredictos de politica, precedentes historicos, logs, perfil de riesgo del comercio e historial del cliente — en una justificacion coherente con pasos concretos. Es la segunda llamada LLM del pipeline `/api/analyze/resolve`.

**Cambio critico respecto a versiones anteriores:** En v1.0 y v2.0, el LLM decidia la accion recomendada, el nivel de riesgo y si requeria HITL. En v3.0, estos campos los calcula el codigo (`_determine_outcome()`) y el LLM los recibe como `DECISION DETERMINADA`. La instruccion clave del system prompt es:

> "La decision (recommended_action, risk_level, requires_hitl) ya fue determinada por el sistema de guardrails basado en los veredictos de politica. Tu tarea NO es decidir — es JUSTIFICAR y EXPLICAR la decision usando la evidencia disponible."

### Rol

Analista senior de contracargos en una fintech latinoamericana.

### Especificacion de entrada

| Parametro | Tipo | Descripcion |
|---|---|---|
| `transaction` | `dict` | Registro completo de la transaccion |
| `policy_verdicts` | `str` | JSON string de `PolicyVerdict[]` de v1_policy_eval |
| `similar_cases` | `str` | Precedentes formateados de Qdrant `historical_cases` |
| `log_summary` | `str` | Resumen de anomalias de los logs (generado por Python, no por LLM) |
| `merchant_risk` | `dict` | Perfil de riesgo del comercio |
| `client_history` | `dict` | Historial de contracargos del cliente |
| `motivo` | `str \| None` | Motivo declarado del contracargo |
| `cliente_vip` | `bool` | Si el cliente tiene estatus VIP |
| `precedent_count` | `int` | Numero de precedentes encontrados |
| `log_count` | `int` | Numero total de eventos de log |
| `determined_outcome` | `dict` | Decision determinada por el sistema (action, risk_level, risk_reason, requires_hitl, precedent_summary) |

### Campos deterministas vs campos LLM

| Campo de salida | Quien lo genera | Notas |
|---|---|---|
| `recommended_action` | **Codigo** (override post-LLM) | El LLM debe copiar el valor de DECISION DETERMINADA |
| `risk_level` | **Codigo** (override post-LLM) | Idem |
| `requires_hitl` | **Codigo** (override post-LLM) | Idem |
| `policy_verdicts` | **Codigo** (inyectado post-LLM) | Se insertan los veredictos de Call 1 directamente |
| `precedent_summary` | **Codigo** (override post-LLM) | Generado por `_build_precedent_summary()` |
| `justification` | **LLM** | Campo analitico principal — razonamiento sobre evidencias |
| `confidence` | **LLM** | Estimacion de certeza (0.0–1.0) |
| `next_steps` | **LLM** | Pasos concretos derivados de las politicas y precedentes |
| `log_summary` | **LLM** (con input determinista) | El LLM recibe el resumen pre-computado y lo reformula |
| `compensation_applicable` | **LLM** | Evaluacion de SLA segun POL-SLA-004 |
| `compensation_amount_usd` | **LLM** | Maximo USD 15 segun POL-SLA-004 |

Incluso si el LLM devuelve valores diferentes para los campos deterministas, `ResolutionService.resolve()` los sobreescribe con los valores calculados por codigo (lineas 87-93 de `resolution.py`). Esto garantiza que la decision final es siempre determinista, sin importar alucinaciones del LLM.

### Especificacion de salida

```json
{
  "transaction_id": "TXN-XXXXX",
  "recommended_action": "VALOR_DE_DECISION_DETERMINADA",
  "confidence": 0.72,
  "justification": "Analisis estructurado con evidencias y razonamiento",
  "precedent_summary": "COPIA EXACTA de DECISION DETERMINADA",
  "log_summary": "Resumen de anomalias en logs",
  "risk_level": "VALOR_DE_DECISION_DETERMINADA",
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "next_steps": ["Paso 1 concreto", "Paso 2 concreto"],
  "requires_hitl": true,
  "hitl_reason": "Motivo de escalacion o null"
}
```

**Valores de `recommended_action`:**

| Valor | Significado | Condicion determinista |
|---|---|---|
| `REJECT` | Contracargo rechazado | Al menos un BLOCKER en veredictos |
| `PENDING_HITL` | Revision humana requerida | FAILs sin BLOCKER, o requires_human_review=true |
| `APPROVE` | Contracargo aprobado | Solo PASS/WARNING/NOT_APPLICABLE |
| `ESCALATE` | Escalacion a equipo especializado | (reservado para uso futuro) |

**Determinacion de `risk_level`:**

| Nivel | Condicion |
|---|---|
| `BLOCKER` | Al menos un veredicto BLOCKER |
| `HIGH` | fail_count >= 2, o fraud_score < 15 |
| `MEDIUM` | 1 FAIL, o fraud_score < 30 |
| `LOW` | Solo PASS/WARNING/NOT_APPLICABLE, fraud_score >= 30 |

### Reglas estrictas integradas en el prompt

1. Usar EXACTAMENTE los valores de recommended_action, risk_level y requires_hitl de la DECISION DETERMINADA
2. NO incluir policy_verdicts en el JSON — ya fueron evaluados por un modulo separado
3. Citar codigos de politica y su veredicto (PASS/FAIL/BLOCKER)
4. **Prohibido inventar datos** — solo usar valores que aparezcan LITERALMENTE en las secciones de datos
5. `compensation_applicable` es true SOLO si se incumplio el SLA (POL-SLA-004)
6. `compensation_amount_usd` maxima: USD 15
7. `next_steps`: 2 a 5 pasos. Formato: "[verbo] + [dato] + [responsable]"
8. `confidence`: 0.9+ si todos PASS, 0.7-0.9 si hay FAILs claros, 0.5-0.7 si hay datos faltantes
9. Responder UNICAMENTE con JSON valido en espanol
10. Si la transaccion tiene status "Resuelta" o "Cerrada", iniciar justification con "Auditoria de caso cerrado"

### Estructura de la justificacion (campo analitico)

El prompt v3.0 exige una estructura de justificacion en 6 partes (maximo 200 palabras):

1. **Explicacion del riesgo:** No solo copiar risk_reason — explicar POR QUE este nivel de riesgo es adecuado. Distinguir riesgo de politica vs riesgo de fraude
2. **Politicas FAIL/BLOCKER:** Para cada una, citar datos especificos (montos, scores, umbrales) y explicar el impacto
3. **Analisis de precedentes:** No solo listar case_id y outcome — RAZONAR sobre implicaciones:
   - Si precedente similar fue aprobado → "sugiere que casos de [motivo] tienden a resolverse a favor del cliente"
   - Si precedente del MISMO MERCHANT → destacar conexion explicitamente
   - Citar patron y tendencia de la DECISION DETERMINADA
4. **Estrategia:** Conectar patron de precedentes con la decision actual — "dado estos precedentes, la tendencia favorece/no favorece al cliente"
5. **Flags del cliente:** Si hay flags que corroboran un veredicto, citarlos como evidencia indirecta
6. **Conclusion:** Conectar evidencias con la decision en 1 oracion

### Ejemplo de salida esperada

```json
{
  "transaction_id": "TXN-00042",
  "recommended_action": "PENDING_HITL",
  "confidence": 0.72,
  "justification": "Riesgo HIGH por 1 violacion de politica (POL-FRD-001). El riesgo no proviene de fraude sofisticado sino de un fraud_score=4 que incumple el umbral minimo de 30 segun POL-FRD-001. POL-EXC-002 PASS confirma trato VIP con SLA de 5 dias. CB-0020 [MOTIVO SIMILAR] fue aprobado en 2 dias, lo que sugiere que casos de fraude/no reconocido con este perfil tienden a resolverse a favor del cliente. CB-0033 tambien fue aprobado (3d), reforzando el patron: 2/2 precedentes aprobados — tendencia favorable al cliente. Dado este patron favorable, la decision PENDING_HITL permite confirmar el fraud_score antes de seguir la tendencia de aprobacion.",
  "precedent_summary": "CB-0020 [MOTIVO SIMILAR]: cargo no reconocido, aprobado en 2d, merchant=eBay. Relevancia: mismo patron de fraude / no reconocido | CB-0033: fraude tarjeta, aprobado en 3d, merchant=Amazon | Patron: de 2 precedentes, 2 aprobados, 0 rechazados. Motivo similar: 1/2, 1 aprobados",
  "log_summary": "2 WARN: timeout gateway + reintento exitoso.",
  "risk_level": "HIGH",
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "next_steps": [
    "Escalar a supervisor para revision (requires_hitl=true)",
    "Verificar POL-FRD-001 — fraud_score=4 vs umbral 30, confirmar si score bajo refleja riesgo real o anomalia",
    "Solicitar prueba de entrega al comercio — plazo segun POL-CB-003",
    "Notificar al cliente VIP sobre estado del caso y plazo estimado"
  ],
  "requires_hitl": true,
  "hitl_reason": "fraud_score=4 con cliente VIP — requiere validacion de supervisor"
}
```

### Registro de cambios

- **v1.0** (2025-01): Version inicial. 8 reglas estrictas, vocabulario de 4 acciones, tope de compensacion USD 15.
- **v2.0** (2025-07): Extraccion mecanica para Haiku. Campos deterministas calculados externamente pero aun incluidos en las instrucciones del prompt. Instrucciones de precedentes mejoradas.
- **v3.0** (2025-07): Transicion a razonamiento analitico para Sonnet. Seccion `DECISION DETERMINADA` en el template de usuario. El LLM ya no decide — justifica. Justificacion estructurada en 6 partes. Instrucciones de next_steps con formato "[verbo] + [dato] + [responsable] + [plazo]". Coherencia obligatoria (compensation_applicable=false → no mencionar compensacion). Conexion de precedentes por merchant.

---

## Prompt 3: v1_judge (v2.0)

**Archivo:** `api/app/llm/prompts/v1_judge.py`
**Modelo:** Sonnet (Call 3)

**Ruta de ejecucion:** Invocado desde n8n `[Juez de Calidad]` → `POST https://api.anthropic.com/v1/messages`. La ruta FastAPI `/api/analyze/judge` sigue disponible para testing directo.

### Proposito

Actuar como supervisor independiente evaluando la calidad de la resolucion producida por v1_resolution. Implementa el patron LLM-as-Judge. El score del Juez controla tanto la escalacion a analistas como el auto-indexado de nuevos precedentes en Qdrant.

### Rol

Supervisor de calidad de resoluciones de contracargos en una fintech latinoamericana.

### Especificacion de entrada

| Parametro | Tipo | Descripcion |
|---|---|---|
| `full_context` | `dict` | Paquete completo de evidencia: transaccion + politicas + precedentes + logs + comercio + cliente |
| `resolution` | `dict` | JSON de resolucion producido por v1_resolution (limpiado de metadata interna: sin guardrail_warnings, _usage, trace_id) |

### Especificacion de salida

```json
{
  "overall_score": 8.7,
  "criteria": {
    "policy_consistency": 9.2,
    "justification_quality": 8.5,
    "precedent_usage": 8.3,
    "risk_assessment": 9.0,
    "actionability": 8.5
  },
  "approved": true,
  "strengths": ["Fortaleza concreta 1", "Fortaleza concreta 2"],
  "weaknesses": ["Area de mejora concreta 1"]
}
```

### Sistema de rubrica por criterio (v2.0)

El cambio principal de v1.0 a v2.0 es la introduccion de rubricas granulares con niveles de referencia por cada criterio. Esto rompio el techo de 8.6 al darle al Juez un marco concreto para puntuar en lugar de depender de su "intuicion".

#### 1. policy_consistency — Consistencia con las politicas

| Nivel | Descripcion |
|---|---|
| **10.0** | Accion perfecta + todos los veredictos respetados sin excepcion |
| **9.0** | Accion correcta + veredictos citados correctamente, minimas inconsistencias menores |
| **7.0-8.9** | Accion correcta pero algun veredicto no citado o razonamiento impreciso |
| **5.0-6.9** | Accion correcta pero inconsistencias claras (cita datos incorrectos) |
| **1.0-4.9** | Accion incorrecta (APPROVE con BLOCKER, REJECT sin BLOCKER) |

**Regla especial:** APPROVE con BLOCKER activo = `policy_consistency` automaticamente 1.0 (error mas grave posible).

#### 2. justification_quality — Calidad de la justificacion

| Nivel | Descripcion |
|---|---|
| **10.0** | Cada afirmacion respaldada por datos verificables + explicacion de por que importan |
| **9.0** | Cita datos correctos de todas las secciones relevantes con analisis de implicaciones |
| **7.0-8.9** | Cita datos correctos pero sin conectarlos analiticamente |
| **5.0-6.9** | Justificacion vaga con pocos datos especificos |
| **1.0-4.9** | ALUCINACION — datos inventados que no existen en la evidencia |

#### 3. precedent_usage — Uso de precedentes

| Nivel | Descripcion |
|---|---|
| **10.0** | Analiza TODOS los precedentes relevantes, identifica patrones, conecta implicaciones al caso actual |
| **9.0** | Analiza precedentes [MOTIVO SIMILAR] con profundidad + cita patron general |
| **7.0-8.9** | Menciona precedentes y cita outcomes pero sin analisis de implicaciones |
| **5.0-6.9** | Solo lista case_ids sin extraer aprendizajes |
| **1.0-4.9** | Ignora completamente los precedentes disponibles |

#### 4. risk_assessment — Evaluacion de riesgo

| Nivel | Descripcion |
|---|---|
| **10.0** | Risk level correcto + explicacion de POR QUE (distingue riesgo de fraude vs politica) + conexion con decision |
| **9.0** | Risk level correcto + explicacion de la fuente del riesgo |
| **7.0-8.9** | Risk level correcto pero sin explicar la fuente o con explicacion incompleta |
| **5.0-6.9** | Risk level correcto pero justificacion contradictoria |
| **1.0-4.9** | Risk level incorrecto |

#### 5. actionability — Accionabilidad de los pasos

| Nivel | Descripcion |
|---|---|
| **10.0** | Cada paso cita politica o dato especifico + responsable + sin contradicciones + conecta con precedentes |
| **9.0** | Pasos concretos con datos de politicas + sin contradicciones entre next_steps y otros campos |
| **7.0-8.9** | Pasos concretos pero con alguna contradiccion menor |
| **5.0-6.9** | Pasos vagos o inaplicables |
| **1.0-4.9** | Sin next_steps o completamente genericos |

### Calculo de scores

- `overall_score` = promedio aritmetico de los 5 criterios
- `approved` = `true` si `overall_score >= 7.0`
- Scores con granularidad real (8.7, 9.2, 7.3) — no redondear sistematicamente a .0 o .5

### Logica de umbral (usada por el sistema, no por el prompt)

| Rango de score | Resultado |
|---|---|
| >= 8.0 | Aprobado + auto-indexado como nuevo precedente en Qdrant |
| 7.0–7.9 | Aprobado — entregado al analista como resolucion final |
| 5.0–6.9 | No aprobado — enviado a HITL para revision de analista |
| < 5.0 | No aprobado + flag `needs_review=true` en registro de feedback |

### Reglas adicionales del prompt v2.0

1. **Semantica de fraud_score:** fraud_score es escala 0-100 donde ALTO = SEGURO, BAJO = RIESGO. fraud_score=84 significa transaccion segura (84% confianza), fraud_score=4 significa alto riesgo. El prompt lo explicita para que Sonnet no invierta la semantica
2. **PENDING_HITL no es ambiguo:** No penalizar una resolucion por usar PENDING_HITL cuando hay FAILs sin BLOCKER o requires_human_review=true — es el protocolo correcto
3. **Verificacion de datos:** Comparar cada dato citado en la resolucion contra la evidencia proporcionada; si no aparece, es alucinacion
4. **Contradicciones internas:** Si compensation_applicable=false pero un next_step menciona compensar → baja actionability. Coherencia interna es requisito

### Registro de cambios

- **v1.0** (2025-01): Version inicial. 5 criterios de scoring. APPROVE+BLOCKER = automatico 1.0 en policy_consistency.
- **v2.0** (2025-07): Rubrica granular por criterio con niveles de referencia (10.0, 9.0, 7.0-8.9, 5.0-6.9, 1.0-4.9). Semantica explicita de fraud_score (ALTO=SEGURO). Proteccion contra penalizacion incorrecta de PENDING_HITL. Deteccion de contradicciones internas. Verificacion de alucinaciones. Switch a Sonnet para capacidad evaluativa completa.

---

## Analisis de logs (determinista — sin prompt LLM)

**Implementacion:** `api/app/analysis/analyzer.py` → `Analyzer.count_severities()` + `Analyzer.detect_error_patterns()`
**Integracion:** `ResolutionService._summarize_logs()` en `api/app/services/resolution.py`

### Proposito

Analizar los eventos de log de procesamiento de pagos para contar severidades y detectar patrones de anomalia. A diferencia de los tres prompts, el analisis de logs es **determinista** — no utiliza una llamada LLM. La naturaleza estructurada de los eventos de log (severidad, nombre de evento, servicio) hace que el patron matching basado en reglas sea mas confiable, rapido y economico que el analisis por LLM.

### Como funciona

1. `Analyzer.count_severities(logs)` produce `{"ERROR": N, "WARN": N, "INFO": N}`
2. `Analyzer.detect_error_patterns(logs)` escanea 9 patrones de anomalia conocidos por nombre de evento
3. `ResolutionService._summarize_logs()` combina ambas salidas en un resumen de texto que se pasa al prompt v1_resolution como parametro `log_summary`

El LLM (v1_resolution) recibe el resumen pre-computado y lo interpreta junto con la demas evidencia — nunca procesa logs crudos directamente.

### 9 patrones de anomalia detectados (determinista)

| Patron | Descripcion | Politica relacionada |
|---|---|---|
| `MERCHANT_NO_RESPONSE` x2+ | Timeout sistematico del comercio | POL-CB-002 |
| `TIMEOUT_RETRY` | Conectividad o sobrecarga del sistema | — |
| `FRAUD_ALERT + AUTH_DECLINED` | Transaccion bloqueada por fraude | POL-FRD-001 |
| `SESSION_EXPIRED` durante `PAYMENT_INITIATED` | Pago interrumpido por sesion expirada | — |
| `WEBHOOK_FAILED` | Falla de integracion con sistema del comercio | — |
| `DOUBLE_CHARGE_DETECT` | Posible cargo duplicado | — |
| `SLA_BREACH` | Violacion de SLA detectada por sistema | POL-SLA-002 |
| `GEO_ANOMALY` | Anomalia geografica | POL-FRD-002 |
| `AUTH_DECLINED` multiple | Intentos de autorizacion fallidos repetidos | POL-FRD-001 |
| Secuencia de `ERROR` | Fallo sistematico de procesamiento | — |

### Justificacion del diseno

Los eventos de log tienen campos estructurados (`severity`, `event`, `service`, `code`) que mapean directamente a tipos de anomalia conocidos. Usar patron matching en lugar de una llamada LLM elimina un round-trip de API por investigacion (~300ms + costo de tokens) con cero perdida de precision.

---

## Principios de ingenieria de prompts

### Por que modulos Python separados, no strings inline

Cada prompt vive en un archivo dedicado con un comentario de version en la linea 1. Esto habilita:
- Git diff muestra exactamente que cambio entre versiones
- Las funciones `render()` se pueden testear independientemente con unit tests
- Multiples versiones pueden coexistir (`v1_policy_eval.py`, `v2_policy_eval.py`) durante un rollout
- Las herramientas del IDE (linting, busqueda) funcionan normalmente sobre el texto del prompt

### Por que prompts en espanol

El dataset, las politicas y los mensajes de log estan en espanol. Usar prompts en espanol elimina la sobrecarga de traduccion y reduce el riesgo de drift semantico cuando el LLM traduce conceptos internamente. El modelo de embeddings (`voyage-multilingual-2` via Voyage AI) es explicitamente multilingue y maneja espanol de forma nativa.

### Configuracion de temperatura

Los tres prompts corren a `temperature=0.3` (configurable via `CB_LLM_TEMPERATURE`). Este valor balancea el cumplimiento deterministico de politicas (mas cerca de 0.0) con la calidad de justificaciones en lenguaje natural (mas cerca de 0.5). Las tareas puramente factuales (policy_eval, judge) se benefician mas de temperaturas bajas.

### Enforcement de salida estructurada

Todos los prompts instruyen al LLM: "Responde UNICAMENTE con JSON valido. Sin texto adicional." La funcion `parse_json_safely()` en `llm/parsing.py` proporciona un parser de fallback que elimina code fences de markdown y encuentra JSON embebido si el modelo agrega texto envolvente a pesar de la instruccion. Para v1_judge (invocado desde n8n), el nodo `[Extraer Evaluacion — Juez]` usa una expresion robusta: `JSON.parse($json.content[0].text.replace(/```json\n?/g, '').replace(/```\n?/g, '').trim())`.

### Guardrails post-LLM como red de seguridad

Independientemente de lo que el LLM devuelva, `ResolutionService._validate_resolution()` aplica correcciones automaticas:

| Condicion detectada | Correccion |
|---|---|
| APPROVE con BLOCKER activo | Auto-corregido a REJECT + risk BLOCKER (posible alucinacion) |
| risk_level=BLOCKER sin veredictos BLOCKER | Auto-corregido a HIGH + PENDING_HITL |
| REJECT sin veredictos BLOCKER | Auto-corregido a PENDING_HITL (requiere revision humana) |
| Compensacion excede monto original en >10% | Warning registrado |
| Confianza > 0.95 con 2+ violaciones de politica | Warning registrado |

Estas correcciones son el ultimo nivel de defensa y, gracias a la arquitectura v3.0 donde el codigo decide los campos criticos, se activan cada vez con menor frecuencia.
