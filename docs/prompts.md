# Prompts — Agente de Contracargos CIRI

Los prompts se almacenan como modulos Python versionados en `api/app/llm/prompts/`. Cada modulo exporta `SYSTEM`, `USER_TEMPLATE` y una funcion `render()` que devuelve `(system_prompt, user_prompt)` como tupla.

Todas las llamadas LLM — incluyendo el Juez — pasan por FastAPI para observabilidad consistente via Langfuse, versionado de prompts y manejo de errores unificado. El nodo `[Juez de Calidad]` en n8n llama a `POST /api/analyze/judge`, donde el prompt `v1_judge.py` se renderiza y se ejecuta a traves del mismo `AnthropicClient`. La API devuelve el objeto ya validado contra `JudgeEvaluationOutput`; el nodo `[Extraer Evaluacion — Juez]` solo le pone nombre para los nodos de abajo.

---

## Principio central: "El codigo decide, el LLM explica"

De los 13 campos de `ResolutionOutput`, **9 los fija el codigo** y 4 los escribe el modelo. El reparto no es una descripcion: `services/resolution.py` sobrescribe esos nueve despues de la llamada, siempre, y `tests/unit/test_campos_deterministas.py` lo verifica contra el codigo.

| Campo | Quien lo fija | Como |
|---|---|---|
| `recommended_action` | Codigo | `decision.decidir()` — a partir de los veredictos |
| `risk_level` | Codigo | `decision.nivel_de_riesgo()` — conteo BLOCKER/FAIL + fraud_score |
| `requires_hitl` | Codigo | `decision.decidir()` — derivado de la accion |
| `hitl_reason` | Codigo | `decision.decidir()` — por que necesita una persona |
| `precedent_summary` | Codigo | `precedentes.resumir_precedentes()` — patrones + tendencias |
| `log_summary` | Codigo | `precedentes.resumir_logs()` — severidades + `patrones.detect_error_patterns()` |
| `policy_verdicts` | Codigo (sobre la salida del Call 1) | `decision.degradar_blockers_no_habilitados()` sanitiza lo que evaluo v1_policy_eval |
| `compensation_applicable` | Codigo | `decision.compensacion_por_sla()` — lee el SLA ya calculado |
| `compensation_amount_usd` | Codigo | `decision.compensacion_por_sla()` — tope de la politica, acotado al monto |
| `justification` | Modelo (Call 2) | v1_resolution |
| `confidence` | Modelo (Call 2) | v1_resolution |
| `next_steps` | Modelo (Call 2) | v1_resolution |
| `transaction_id` | Modelo (Call 2) | Lo devuelve tal como lo recibio |

`precedent_summary` y `log_summary` ademas **entran al prompt** como contexto: el modelo los recibe calculados para poder razonar sobre ellos, y su copia se descarta. Hasta v3.1 el prompt se los pedia y la version del modelo era la que quedaba, asi que el informe podia mostrar un resumen de logs que no coincidia con los logs impresos al lado.

El LLM recibe la decision ya tomada en la seccion `DECISION DETERMINADA` del prompt y su tarea es justificarla con evidencia, no tomarla. Esto elimina la categoria entera de errores donde el LLM elige una accion incorrecta (por ejemplo, APPROVE con BLOCKER activo).

### Logica determinista en detalle

```python
# Archivo: api/app/services/resolution.py — decision.decidir()

BLOCKER en verdicts         → REJECT + risk BLOCKER
FAIL sin BLOCKER            → PENDING_HITL + risk HIGH o MEDIUM
requires_human_review=true  → PENDING_HITL (red de seguridad)
Solo PASS/WARNING           → APPROVE + risk LOW o MEDIUM
```

Ademas, `decision.degradar_blockers_no_habilitados()` degrada cualquier BLOCKER emitido por el LLM para politicas fuera de `puede_bloquear` (actualmente solo `POL-EXC-003`) a FAIL + `requires_human_review=true`. Esto previene la sobre-escalacion del LLM (por ejemplo, asignar BLOCKER a un comercio suspendido, que no es tecnicamente irreversible).

---

## Estrategia dual-model

El pipeline utiliza dos modelos Claude para optimizar costo vs. calidad:

| Llamada | Modelo | Razon |
|---|---|---|
| Call 1: Evaluacion de politicas (v1.5) | **Haiku** | Tarea mecanica: comparar datos contra reglas. Haiku es rapido y suficiente. |
| Call 2: Sintesis de resolucion (v3.2) | **Sonnet** | Tarea analitica: razonar sobre precedentes, conectar evidencias, justificar. |
| Call 3: Juez de calidad (v2.2) | **Sonnet** | Tarea evaluativa: aplicar rubrica detallada, detectar inconsistencias. |

Configuracion en `.env`:
```
CB_LLM_MODEL=claude-haiku-4-5-20251001       # Call 1
CB_LLM_MODEL_RESOLUTION=claude-sonnet-4-6         # Call 2 + Call 3
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

**De v2.0 a v3.0 — la transicion critica:** En v2.0, el prompt de resolucion pedia al LLM que extrajera datos mecanicamente (copiar campos, listar politicas). Esto era una tarea donde Haiku era suficiente pero el resultado carecia de profundidad analitica. En v3.0, al mover los campos deterministas al codigo, el prompt se libero para pedir razonamiento genuino: "explica POR QUE este nivel de riesgo es adecuado", "RAZONA sobre las implicaciones de los precedentes", "conecta el patron de precedentes con la decision actual". Sonnet responde a estas instrucciones con analisis que Haiku no puede producir.

---

## Historial de versiones

| Prompt | Version | Fecha | Resumen |
|---|---|---|---|
| v1_policy_eval | v1.0 | 2025-01 | Version inicial — 5 veredictos, reglas Cripto=BLOCKER y FRD-001 |
| v1_policy_eval | v1.2 | 2025-07 | Logica matematica de umbrales (>, >=, <), determinacion LATAM, contexto de comercio/cliente, documentacion, ventanas temporales |
| v1_policy_eval | v1.3 | 2026-08 | La lista de paises LATAM se interpola desde `domain.enums`, no esta escrita en el texto |
| v1_policy_eval | v1.4 | 2026-08 | Quien puede bloquear lo dice la marca `[PUEDE BLOQUEAR]` de cada politica, no su codigo escrito en las reglas |
| v1_policy_eval | v1.5 | 2026-08 | Recibe los plazos ya medidos (`check_sla`); no se declara cumplimiento por falta de prueba; la region sale del pais de la transaccion, que es el unico que existe |
| v1_resolution | v1.0 | 2025-01 | Version inicial — 8 reglas estrictas, vocabulario de 4 acciones |
| v1_resolution | v2.0 | 2025-07 | Extraccion mecanica para Haiku — campos deterministas calculados externamente |
| v1_resolution | v3.0 | 2025-07 | Razonamiento analitico para Sonnet — "el codigo decide, el LLM explica" |
| v1_resolution | v3.1 | 2025-08 | El SLA entra al contexto y la compensacion pasa a ser determinista (POL-SLA-004) |
| v1_resolution | v3.2 | 2026-08 | `log_summary` y `precedent_summary` los fija el codigo; el prompt deja de pedirlos y el modelo deja de parafrasearlos |
| v1_judge | v1.0 | 2025-01 | Version inicial — 5 criterios, APPROVE+BLOCKER = 1.0 automatico |
| v1_judge | v2.0 | 2025-07 | Rubrica granular por criterio (niveles 10.0, 9.0, 7.0-8.9, etc.), semantica de fraud_score, proteccion contra penalizacion incorrecta de PENDING_HITL |
| v1_judge | v2.1 | 2025-08 | `policy_consistency` y `risk_assessment` evaluan la propuesta del modelo, no la version ya corregida por el override |
| v1_judge | v2.2 | 2026-08 | El umbral de aprobacion se interpola desde `domain.constants`, no esta escrito en el texto |

---

## Prompt 1: v1_policy_eval (v1.5)

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
| `sla` | `dict` | Los plazos del caso **ya medidos** por `check_sla`: apertura del reclamo, corte, dias habiles transcurridos contra el limite, y los dias corridos entre la compra y el reclamo |

**Por que el `sla` entra al prompt (v1.5).** Estaba calculado dos pasos antes y solo se
usaba para decidir la compensacion. El evaluador no lo veia, asi que respondia «no se
proporciona la fecha de inicio del reclamo» sobre un dato que el sistema tenia, y las
politicas de plazo caian en WARNING — cada uno con `requires_human_review`, o sea que el
caso se derivaba a una persona por como se armaba el contexto y no por su riesgo. En
TXN-00006 eso eran 8 WARNING; con los plazos a la vista quedan 2, y POL-CB-001,
POL-SLA-002 y POL-SLA-003 pasan a PASS citando el numero contra el umbral.

**Las cuentas las hace el codigo.** Los dos plazos —el de disputa, en dias corridos desde
la compra, y el de resolucion, en dias habiles desde la apertura— llegan contados. Al
prompt se le paso un rato la instruccion de restar fechas, y estaba mal por dos motivos:
una resta de fechas es trabajo determinista, y en 24 de las 47 transacciones con caso el
reclamo figura antes de la compra —ruido del dataset sintetico—, asi que la cuenta daba
negativa la mitad de las veces. Ahora `check_sla` devuelve `dias_hasta_el_reclamo` o,
cuando las fechas no se ordenan, `fechas_inconsistentes: true`.

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
| `BLOCKER` | Violacion critica — la transaccion es TECNICAMENTE IRREVERSIBLE. Reservado a las politicas marcadas `[PUEDE BLOQUEAR]` (hoy solo POL-EXC-003, por la semilla `POLICY_SEED_BLOQUEANTES`; la marca sale de la columna `puede_bloquear`, editable). Un comercio suspendido o un cliente riesgoso NO son BLOCKER |
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
2. **Quien puede bloquear lo dice la politica, no su codigo:** solo las politicas marcadas `[PUEDE BLOQUEAR]` en su encabezado pueden recibir `BLOCKER`, y ademas su condicion tiene que cumplirse — marcada + condicion que no se cumple = `PASS`. Una politica sin la marca cuya condicion si se cumple es `FAIL`, con `requires_human_review=true` si el caso necesita una persona
3. Las condiciones —umbrales, metodos de pago, plazos— se leen de la descripcion de cada politica: no aplicar una condicion que la politica no diga, ni recordar reglas de politicas que no esten en la lista
4. Un `BLOCKER` significa que la resolucion final DEBE rechazar el contracargo
5. Evaluar TODAS las politicas proporcionadas — no omitir ninguna
6. Usar TODOS los datos disponibles: transaccion, perfil de riesgo del comercio e historial del cliente
7. `NOT_APPLICABLE` solo cuando la politica genuinamente no aplica; comercios suspendidos siguen siendo relevantes para politicas de plazos
8. Responder UNICAMENTE con un array JSON valido, sin texto adicional
9. **Determinacion de region:** siempre por el campo `country` de la TRANSACCION, tambien cuando la politica habla de "comercios fuera de LATAM". La lista de paises LATAM se inyecta desde `domain/enums.py`, no se escribe en el prompt: cuando estaba escrita, decia 20 paises y el enum tenia 7, asi que para un ECU el codigo aplicaba plazo extendido de no-LATAM y el LLM leia que ECU si era LATAM. Hasta v1.4 la regla ademas pedia el *pais del comercio* y mandaba WARNING si no constaba — y no consta nunca: no existe esa columna ni esa tabla, asi que la politica de plazos extendidos quedaba en WARNING permanente por un dato inexistente, mientras el EJEMPLO 3 del mismo prompt la resolvia por el pais de la transaccion. Es ademas el criterio que aplica `check_sla`, que es lo que evita que el veredicto y el plazo medido se contradigan
10. **Documentacion:** Si una politica requiere documentacion y se marca WARNING, especificar que documentos faltan y si bloquean la decision
11. **No se declara cumplimiento por falta de prueba:** `PASS` significa que los datos muestran que la condicion de violacion no se cumple; si el dato necesario no esta, es `WARNING`. "No hay registro de que se haya excedido el plazo" no es un PASS. Y la simetria vale: con el dato presente y la condicion no cumplida, el `PASS` es obligatorio — la regla no es "evitar el PASS". El veredicto tiene que coincidir con el propio razonamiento, y un `FAIL` exige que la condicion *de esa politica* se cumpla, no que otra haya fallado
12. **Plazos:** los dos llegan contados por `check_sla` — el de disputa en dias corridos (`dias_hasta_el_reclamo`), el de resolucion en dias habiles (`days_elapsed`)—. El prompt no resta fechas: si `fechas_inconsistentes` es true, el plazo de disputa no se evalua y corresponde WARNING

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
### Politica 1 [PUEDE BLOQUEAR] (relevancia: NN%)
- Codigo: POL-EXC-003
- Categoria: EXCEPCION
- Nombre: Exclusion de criptomonedas
- Descripcion: Las transacciones realizadas con criptomonedas son irreversibles...
- Referencia: Reg. Fintech 2024/03

### Politica 2 (relevancia: NN%)
- Codigo: POL-FRD-001
- Categoria: FRAUDE
- Nombre: Umbral antifraude
- Descripcion: Transacciones con score < 15 requieren revision manual obligatoria...
...

Evalua cada politica usando TODOS los datos disponibles y devuelve el array JSON.
```

El bloque de politicas lo arma `format_policies_for_prompt` (`api/app/rag/formatter.py`): el encabezado numerado lleva `[PUEDE BLOQUEAR]` solo cuando la politica esta habilitada para bloquear, y esa es la marca que busca la regla 2. La relevancia va como porcentaje del score de Qdrant y cambia con cada consulta, por eso aca queda como `NN%`.

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

### Guardrail post-LLM: `decision.degradar_blockers_no_habilitados()`

Despues de recibir los veredictos del LLM, el sistema aplica una sanitizacion determinista: cualquier veredicto `BLOCKER` para una politica fuera de `puede_bloquear` (actualmente solo `POL-EXC-003`) se degrada a `FAIL` con `requires_human_review=true`. Esto previene que Haiku sobre-escale situaciones que son graves pero no tecnicamente irreversibles.

### Registro de cambios

- **v1.0** (2025-01): Version inicial. Sistema de 5 veredictos. Reglas Cripto=BLOCKER y FRD-001 hardcoded.
- **v1.2** (2025-07): Logica matematica de umbrales con operadores explicitos (>, >=). Determinacion LATAM (pais de transaccion vs pais del comercio). Contexto enriquecido con perfil de riesgo del comercio e historial del cliente. Reglas de documentacion faltante. Ventanas temporales. Ejemplos de evaluacion correcta.

---

## Prompt 2: v1_resolution (v3.2)

**Archivo:** `api/app/llm/prompts/v1_resolution.py`
**Modelo:** Sonnet (Call 2)

### Proposito

Justificar y explicar una decision de contracargo que ya fue determinada por el sistema de guardrails. El LLM sintetiza la evidencia disponible — veredictos de politica, precedentes historicos, logs, perfil de riesgo del comercio e historial del cliente — en una justificacion coherente con pasos concretos. Es la segunda llamada LLM del pipeline `/api/analyze/resolve`.

**Cambio critico respecto a versiones anteriores:** En v1.0 y v2.0, el LLM decidia la accion recomendada, el nivel de riesgo y si requeria HITL. En v3.0, estos campos los calcula el codigo (`decision.decidir()`) y el LLM los recibe como `DECISION DETERMINADA`. La instruccion clave del system prompt es:

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
| `hitl_reason` | **Codigo** (override post-LLM) | `decision.decidir()` — se sobrescribe cuando la decision trae motivo (`resolution.py` 157-158) |
| `policy_verdicts` | **Codigo** (inyectado post-LLM) | Se insertan los veredictos de Call 1 directamente |
| `precedent_summary` | **Codigo** (override post-LLM) | Generado por `precedentes.resumir_precedentes()` |
| `justification` | **LLM** | Campo analitico principal — razonamiento sobre evidencias |
| `confidence` | **LLM** | Estimacion de certeza (0.0–1.0) |
| `next_steps` | **LLM** | Pasos concretos derivados de las politicas y precedentes |
| `log_summary` | **Codigo** (override post-LLM) | Generado por `precedentes.resumir_logs()`; el modelo lo recibe como contexto y su copia se descarta |
| `compensation_applicable` | **Codigo** (override post-LLM) | `decision.compensacion_por_sla()` — lee el SLA ya calculado (POL-SLA-004) |
| `compensation_amount_usd` | **Codigo** (override post-LLM) | `decision.compensacion_por_sla()` — tope de POL-SLA-004, acotado al monto |
| `transaction_id` | **LLM** | Lo devuelve tal como lo recibio |

Incluso si el LLM devuelve valores diferentes para los campos deterministas, `ResolutionService.resolve()` los sobreescribe con los valores calculados por codigo (lineas 146-161 de `resolution.py`). Esto garantiza que la decision final es siempre determinista, sin importar alucinaciones del LLM.

### Especificacion de salida

```json
{
  "transaction_id": "TXN-XXXXX",
  "recommended_action": "VALOR_DE_DECISION_DETERMINADA",
  "confidence": 0.72,
  "justification": "Analisis estructurado con evidencias y razonamiento",
  "precedent_summary": "COPIA EXACTA de DECISION DETERMINADA",
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
5. `compensation_applicable` y `compensation_amount_usd`: si aparecen en la DECISION DETERMINADA, copiarlos EXACTAMENTE — el sistema ya los calculo contando dias habiles contra el limite de POL-SLA-004, no se recalculan ni se discuten. Si no aparecen, no hubo dato de SLA: quedan en false y 0.0
6. Si `compensation_applicable` es true, explicar en `justification` por que, citando los dias transcurridos, el limite y la politica de la seccion CUMPLIMIENTO DE SLA
7. `next_steps`: 2 a 5 pasos. Formato: "[verbo] + [dato] + [responsable]"
8. `confidence`: 0.9+ si todos PASS, 0.7-0.9 si hay FAILs claros, 0.5-0.7 si hay datos faltantes
9. Responder UNICAMENTE con JSON valido en espanol
10. Si la transaccion tiene status "Resuelta" o "Cerrada", iniciar justification con "Auditoria de caso cerrado"

### Estructura de la justificacion (campo analitico)

El prompt exige una estructura de justificacion en 6 partes (maximo 200 palabras):

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
- **v3.1** (2025-08): El resultado de `POST /api/sla/check` entra al prompt como seccion `CUMPLIMIENTO DE SLA`, y `compensation_applicable` / `compensation_amount_usd` pasan a `DECISION DETERMINADA`. Hasta v3.0 la regla decia "compensation_applicable es true SOLO si se incumplio el SLA" mientras el modelo no recibia ningun dato de SLA: se le pedia una decision a ciegas sobre un calculo que el codigo ya hacia bien, en dias habiles. Ahora la decide `decision.compensacion_por_sla` y el modelo la explica.

---

## Prompt 3: v1_judge (v2.2)

**Archivo:** `api/app/llm/prompts/v1_judge.py`
**Modelo:** Sonnet (Call 3)

**Ruta de ejecucion:** Invocado desde n8n `[Juez de Calidad]` via `POST /api/analyze/judge` en FastAPI. Todas las llamadas LLM (incluyendo el Juez) pasan por FastAPI para observabilidad Langfuse y versionado de prompts consistente.

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
| 5.0–6.9 | No aprobado — el informe sale igual, marcado (ver la nota de abajo) |
| < 5.0 | No aprobado — mismo marcado que la banda anterior, mas el flag `needs_review=true` en el registro de feedback |

El marcado no distingue bandas: la salida `false` de `¿Juez Aprueba?` —es decir, cualquier `overall_score` por debajo de `JUDGE_APPROVAL_THRESHOLD` (7.0)— pasa por el nodo Set `Marcar — Calidad Baja`, que escribe en `judge_evaluation.quality_flag` el string completo `"LOW_QUALITY — Revisar resolución manualmente"` (buscarlo por igualdad exacta con `"LOW_QUALITY"` no lo encuentra). Un score bajo no deriva a HITL: eso lo decide `resolution.requires_hitl` en el `Switch — Derivacion`.

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

**Implementacion:** `api/app/analysis/patrones.py` → `count_severities()` + `detect_error_patterns()` (funciones libres, no metodos de `Analyzer`: no tocan la base)
**Integracion:** `precedentes.resumir_logs()`, llamada desde `ResolutionService.resolve()` en `api/app/services/resolution.py`

### Proposito

Analizar los eventos de log de procesamiento de pagos para contar severidades y detectar patrones de anomalia. A diferencia de los tres prompts, el analisis de logs es **determinista** — no utiliza una llamada LLM. La naturaleza estructurada de los eventos de log (severidad, nombre de evento, servicio) hace que el patron matching basado en reglas sea mas confiable, rapido y economico que el analisis por LLM.

### Como funciona

1. `patrones.count_severities(logs)` produce `{"ERROR": N, "WARN": N, "INFO": N}`
2. `patrones.detect_error_patterns(logs)` escanea 8 patrones de anomalia conocidos por nombre de evento
3. `precedentes.resumir_logs()` combina ambas salidas en un resumen de texto que se pasa al prompt v1_resolution como parametro `log_summary`

El LLM (v1_resolution) recibe el resumen pre-computado y lo interpreta junto con la demas evidencia — nunca procesa logs crudos directamente.

### 8 patrones de anomalia detectados (determinista)

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

Son los ocho miembros de `ErrorPattern` (`domain/enums.py`) y las ocho ramas de `detect_error_patterns`: la tabla y el enum se cuentan igual.

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

Todos los prompts instruyen al LLM: "Responde UNICAMENTE con JSON valido. Sin texto adicional." La funcion `parse_json_safely()` en `llm/parsing.py` proporciona un parser de fallback que elimina code fences de markdown y encuentra JSON embebido si el modelo agrega texto envolvente a pesar de la instruccion. Para v1_judge, el parseo ocurre del lado de la API: `POST /api/analyze/judge` devuelve el objeto ya validado contra `JudgeEvaluationOutput`. El nodo `[Extraer Evaluacion — Juez]` es un `Set` de una sola linea (`judge_evaluation = {{ $json }}`) que le pone nombre al resultado para los nodos de abajo. Extraer JSON de la respuesta cruda del modelo dentro de una expresion de n8n seria mover a un nodo lo que ya esta cubierto por tests en `llm/parsing.py`.

### Guardrails post-LLM como red de seguridad

Independientemente de lo que el LLM devuelva, el codigo fija los campos criticos a partir de los veredictos de politica. Lo que hacen los guardrails es dejar constancia de la discrepancia:

| Condicion detectada | Donde | Que hace |
|---|---|---|
| El modelo propuso APPROVE con un BLOCKER activo | `guardrails.antes_del_override` | Registra la contradiccion; el override ya fijo REJECT |
| El modelo propuso risk_level=BLOCKER sin veredictos BLOCKER | `guardrails.antes_del_override` | Registra; el override ya fijo el riesgo real |
| El modelo propuso REJECT sin veredictos BLOCKER | `guardrails.antes_del_override` | Registra; el override ya derivo a revision humana |
| El modelo contradijo al SLA sobre la compensacion | `guardrails.antes_del_override` | Registra; el override ya fijo lo que dice POL-SLA-004 |
| Compensacion excede el monto original en >10% | `guardrails.despues_del_override` | Warning sobre un campo que el modelo si controla |
| Confianza > 0.95 con 2+ violaciones de politica | `guardrails.despues_del_override` | Warning sobre un campo que el modelo si controla |

Los cuatro primeros corren **antes** del override determinista. Es la unica ventana en la que la
propuesta del modelo existe: despues, la contradiccion ya fue reemplazada por la decision del
codigo y no habria nada que detectar.
