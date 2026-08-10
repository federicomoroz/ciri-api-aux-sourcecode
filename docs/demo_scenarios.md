# Escenarios de Demostración — Agente de Contracargos CIRI

Tres escenarios end-to-end que demuestran los comportamientos centrales del sistema: rechazo automático por BLOCKER, escalamiento HITL (Human-in-the-Loop) para casos ambiguos de alto riesgo, y detección de SLA extendido para transacciones fuera de LATAM.

---

## Requisitos previos

### Opción 1: Demo en vivo (sin instalación)

El panel interactivo está desplegado en Render:

```
https://ciri-chargeback-agent.onrender.com/panel
```

Elegí un caso y hacé clic en **Analizar**.

> **Sobre el modo demo.** El panel arranca en modo demo y el toggle de arriba lo cambia. En modo demo **el pipeline corre entero, de verdad**: el deploy tiene configurado un modelo con free tier (`gemini-flash-lite-latest`) y la clave del servidor, así que se puede analizar cualquier transacción del dataset sin que le cueste a nadie. El informe abre con un cartel **ANÁLISIS REAL (modelo gratuito)** que nombra el modelo y declara que la nota del juez puede desviarse hasta ±2.5 puntos respecto de la configuración documentada.
>
> Si no hay free tier configurado, el modo demo **no llama al modelo**: los tres escenarios de este documento se sirven con su análisis ya calculado, al instante y sin costo. Ahí el informe abre con un cartel **DEMO (Caso prearmado)** y la respuesta trae `X-Modo-Demo: true`, así que nunca se confunde con un análisis recién hecho.
>
> Para verlo en la configuración documentada (Haiku para políticas, Sonnet para síntesis y juez): apagá el toggle (**Modo producción**) y cargá tu clave de Anthropic en el campo **API key**. Esa clave reemplaza a la del servidor y podés analizar cualquier transacción del dataset, gastando de tu cuenta.
>
> Si pedís un caso que no tiene análisis guardado y el modelo no está disponible, se responde con el **más cercano en riesgo** de los tres —se compara el score antifraude— y el cartel nombra las dos transacciones para que quede claro de cuál es el informe. El porqué está en [`decisions.md`](decisions.md), decisión 14.

### Opción 2: Stack local

```bash
docker-compose up -d
python scripts/seed_data.py    # Excel → SQLite + Qdrant (ejecutar solo la primera vez)
```

El panel de pruebas está disponible en `http://localhost:8000/panel`.

### El formulario, como segunda vía de entrada

`http://localhost:5678/form/chargeback-form` — al enviar el formulario, ese workflow llama al webhook del orquestador, así que el caso corre los 36 pasos igual que por el webhook.

Para probarlo sin navegador hay un detalle: n8n nombra los inputs `field-0`, `field-1`, `field-2` por posición, no por su etiqueta.

```bash
curl -X POST http://localhost:5678/form/chargeback-form   -F "field-0=TXN-00051" -F "field-1=No reconoce la compra"
```

Con un identificador mal formado (`ABC-123`) la ejecución toma la rama de validación, responde explicando el formato y registra un `WARN` en el log operativo — sin pasar por el error handler, porque un tipeo no es una falla del sistema. Para que el stack local ejecute siempre de verdad en vez de arrancar en demo, poné `CB_DEMO_MODE=false` en el `.env`.

### Panel de pruebas (`/panel`)

La forma más fácil de probar cualquier escenario es el panel interactivo en `/panel`. El panel ofrece 3 modos de pipeline: **Directo (sin n8n)** (default), **n8n Test** y **n8n Production**. En modo directo, el panel usa SSE streaming para mostrar el progreso en tiempo real con datos reales de cada paso (nombre del comercio, políticas recuperadas, desglose de veredictos, score del juez, etc.). En modo n8n, se muestra un spinner con "Esperando respuesta de n8n...". Solo se necesita ingresar el `transaction_id` y opcionalmente el motivo del contracargo.

---

## Modelo dual de LLM

El sistema utiliza una estrategia de dos modelos para optimizar costo y calidad:

| Etapa | Modelo | Justificación |
|-------|--------|---------------|
| Evaluación de políticas (call 1) | `claude-haiku-4-5` | Rápido y económico — evalúa 17 políticas contra datos estructurados |
| Síntesis de resolución (call 2) | `claude-sonnet-4-6` | Mayor capacidad de razonamiento para generar justificaciones citadas |
| Juez de calidad (call 3) | `claude-sonnet-4-6` | Evaluación crítica — requiere juicio calibrado (5 criterios, score 1-10) |

Los tres escenarios de esta página promedian **8.7/10** — son los tres casos más contenciosos del dataset, elegidos por cubrir los dos desenlaces del enrutador —rechazo automático (`TXN-00051`, BLOCKER) y revisión humana (`TXN-00042` y `TXN-00089`, los dos HIGH)— y tres situaciones de política distintas: el blocker de cripto, el cliente VIP con score de fraude, y el SLA extendido fuera de LATAM. No por su puntaje. Sobre el conjunto de corridas de desarrollo el promedio fue **9.1/10**; la metodología está en [`mejora_continua.md`](mejora_continua.md#como-se-midio-el-91).

---

## Escenario 1: TXN-00051 — Cripto + Fraude → Rechazo Automático (BLOCKER)

### Qué demuestra

La capacidad del sistema para aplicar exclusiones de política no negociables. Las transacciones con criptomonedas son irreversibles por definición (POL-EXC-003). Combinado con un fraud_score de 8/100 (POL-FRD-001, umbral mínimo 30), este caso produce un BLOCKER y tres FAIL. **Un solo BLOCKER alcanza**: `puede_bloquear` contiene únicamente a POL-EXC-003, así que cualquier otro veredicto bloqueante que emita el modelo se degrada a FAIL con revisión humana. La resolución debe ser `REJECT` sin importar cualquier otra evidencia. Este escenario también muestra el guardrail: si el LLM alucinara un `APPROVE`, el sistema lo corrige automáticamente.

### Perfil de la transacción

| Campo | Valor |
|-------|-------|
| ID | TXN-00051 |
| Comercio | Airbnb |
| Monto | USD 2.095,90 |
| Método de pago | Cripto |
| País | COL |
| Fraud score | 8 / 100 |
| Canal | POS |
| Cliente VIP | No |

> Los valores de esta sección salen de `data/chargeback.db` y el resultado, de
> `data/informes_demo/analisis_TXN-00051.json`, que viaja en este mismo paquete.

### Flujo esperado del pipeline

```
1. Webhook/Panel recibe {"transaction_id": "TXN-00051"}
2. GET /api/transactions/TXN-00051        → datos de la transacción
3. GET /api/logs/TXN-00051                → logs asociados
4. GET /api/policies/search               → RAG semántico → recupera 17 políticas
5. GET /api/cases/similar                 → RAG semántico → precedentes similares
6. GET /api/merchants/Airbnb/risk         → perfil de riesgo del comercio
7. GET /api/clients/{id}/history          → historial del cliente
8. POST /api/sla/check                    → verificación SLA (10 días LATAM)
9. POST /api/analyze/resolve              → LLM evalúa políticas (Haiku) + sintetiza (Sonnet)
   → POL-EXC-003: BLOCKER (cripto irreversible — única política de la whitelist)
   → POL-FRD-001, POL-CB-003, POL-CB-004: FAIL
   → Acción determinística: REJECT (hay un BLOCKER)
10. POST /api/analyze/judge               → LLM-as-Judge (Sonnet) → score 8.6/10
11. POST /api/reports/html                → Reporte HTML con badge BLOCKER rojo
```

### Comandos curl paso a paso

```bash
# Paso 1: Verificar que la transacción existe
curl -s http://localhost:8000/api/transactions/TXN-00051 | jq .

# Paso 2: Ver qué políticas recupera el RAG (el QueryBuilder enriquece la query automáticamente)
curl -s "http://localhost:8000/api/policies/search?payment_method=Cripto&fraud_score=8&country=COL" \
  | jq '{query: .query_used, count: .count}'

# Paso 3: Buscar precedentes similares
curl -s "http://localhost:8000/api/cases/similar?merchant=Airbnb&amount_usd=2095.90&payment_method=Cripto&country=COL&fraud_score=8" \
  | jq '.results[] | {case_id, resolution, fraud_score}'

# Paso 4a: Investigación completa vía webhook n8n (ruta principal)
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte_blocker.html

# Paso 4b (alternativa): Panel interactivo — abrir en el navegador:
#   http://localhost:8000/panel
#   Ingresar TXN-00051 → clic en "Analizar"

# Paso 4c (alternativa): Demo en vivo en Render:
#   https://ciri-chargeback-agent.onrender.com/panel
```

### Salida real

No es una salida esperada: es la que produjo el sistema, recortada. El archivo
completo —con los 17 veredictos y la evaluación del Juez— viaja en
`data/informes_demo/analisis_TXN-00051.json`.

```json
{
  "transaction_id": "TXN-00051",
  "recommended_action": "REJECT",
  "confidence": 0.95,
  "risk_level": "BLOCKER",
  "justification": "El riesgo BLOCKER no proviene de fraude sofisticado sino de una restricción estructural e irreversible: el método de pago es Cripto, lo que activa POL-EXC-003 como BLOCKER automático. Las transacciones en criptomonedas son técnicamente irreversibles y no son elegibles para chargeback bajo ninguna ci…",
  "policy_verdicts": [
    {
      "policy_code": "POL-EXC-003",
      "verdict": "BLOCKER",
      "reasoning": "Método de pago es Cripto (irreversible). BLOCKER automático según POL-EXC-003. Las transacciones en criptomonedas no son elegibles para chargeback bajo ninguna circunstancia."
    },
    {
      "policy_code": "POL-FRD-001",
      "verdict": "FAIL",
      "reasoning": "fraud_score=8, umbral mínimo=30. 8 < 30 → transacción con score antifraude inferior al umbral. Según POL-FRD-001, debe ser rechazada automáticamente o derivada a revisión manual. Requiere acción inmediata."
    },
    {
      "policy_code": "POL-CB-004",
      "verdict": "FAIL",
      "reasoning": "Comercio Airbnb: total_transactions=4, total_chargebacks=3, cb_ratio=0.75 (75%). Umbral crítico: >2% (suspensión preventiva). 75% >> 2% → comercio supera ampliamente el límite de CBs. Además, perfil indica flag 'suspended_merchant', confirmando que ya está suspendido. Política violada."
    },
    {
      "policy_code": "POL-CB-003",
      "verdict": "FAIL",
      "reasoning": "Comercio está suspendido (flag='suspended_merchant'). POL-CB-003 requiere que el comercio presente defensa en 10 días hábiles. Un comercio suspendido no puede cumplir esta obligación. La política sigue siendo relevante para el procesamiento del chargeback, pero la suspensión impide que el comercio responda adecuadamente. Requiere revisión manual sobre cómo proceder con comercio suspendido."
    }
  ],
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "requires_hitl": false,
  "next_steps": [
    "Notificar al cliente CLI-0003 que la transacción TXN-00051 no es elegible para chargeback por método de pago Cripto (irreversible) según POL-EXC-003 — área de Atención al Cliente, plazo inmediato",
    "Registrar el REJECT formal en el sistema citando BLOCKER POL-EXC-003 como causal única y suficiente — área de Operaciones de Contracargos, plazo 24h hábiles",
    "Escalar el perfil del comercio Airbnb (cb_ratio=0.75, flag 'suspended_merchant') al área de Gestión de Comercios para revisión de suspensión vigente según POL-CB-004 — plazo 48h hábiles",
    "Solicitar timestamps de apertura del reclamo y primera respuesta al cliente para verificar cumplimiento de POL-SLA-001 (48h hábiles) — área de Operaciones, dato complementario que no modifica el REJECT actual pero es requerido para auditoría",
    "Documentar los 2 eventos MERCHANT_NO_RESPONSE en el expediente del comercio Airbnb como evidencia adicional de incumplimiento de SLA comercial — área de Riesgo de Comercios, plazo 48h hábiles"
  ],
  "guardrail_warnings": []
}
```

### Observaciones clave

1. **Un BLOCKER y tres FAIL:** POL-EXC-003 (cripto = irreversible) es el único que puede bloquear — es el único código en `puede_bloquear`. POL-FRD-001 (score 8 < 30), POL-CB-003 y POL-CB-004 quedan en FAIL. La acción `REJECT` se determina de forma determinística antes de que el LLM sintetice la justificación.
2. **Guardrail no activado:** El LLM produce correctamente `REJECT` — el guardrail no tiene nada que corregir. Si hubiera dicho `APPROVE`, el sistema lo habría sobrescrito.
3. **Sin HITL:** Los casos BLOCKER son determinísticos — la revisión del analista no agrega valor.
4. **Sin compensación:** El SLA no fue incumplido (el caso se rechazó inmediatamente).
5. **Score del Juez:** 8.6/10 (`data/informes_demo/analisis_TXN-00051.json`) — alta puntuación porque la resolución cita correctamente ambas políticas con datos específicos y la acción es consistente con los veredictos.
6. **Alerta emitida:** El pipeline emite una alerta `blocker_auto_reject` (severidad ERROR) via `POST /api/alerts/`, visible en el log de alertas del panel.

---

## Escenario 2: TXN-00042 — Crédito Visa + Score Bajo + VIP → HITL (Revisión Analista)

### Qué demuestra

La ruta de escalamiento Human-in-the-Loop del sistema. Cuando el fraud_score=4 indica alto riesgo y hay veredictos FAIL pero no BLOCKER (Crédito Visa es reversible), la resolución no puede ser auto-aprobada ni auto-rechazada. El caso requiere juicio humano. Este escenario muestra cómo el sistema gestiona la tensión entre riesgo de fraude y política de retención de clientes VIP, delegando correctamente al analista.

### Perfil de la transacción

| Campo | Valor |
|-------|-------|
| ID | TXN-00042 |
| Comercio | Airbnb |
| Monto | USD 2.055,76 |
| Método de pago | Crédito Visa |
| País | BRA |
| Fraud score | 4 / 100 |
| Canal | Web |
| Cliente VIP | Sí |

### Flujo esperado del pipeline

```
1. Webhook/Panel recibe {"transaction_id": "TXN-00042", "cliente_vip": true}
2. Recopilación de contexto (7 llamadas HTTP en paralelo)
3. POST /api/analyze/resolve → LLM evalúa políticas (Haiku) + sintetiza (Sonnet)
   → POL-FRD-001: FAIL (score 4 < umbral 30, pero NO es BLOCKER — Visa es reversible)
   → Sin BLOCKERs → acción no determinística
   → fraud_score < 15 (`RISK_FRAUD_SEVERE`) → riesgo HIGH
   → Acción: PENDING_HITL (score crítico + VIP = requiere juicio humano)
4. POST /api/analyze/judge → Juez (Sonnet) → score 8.7/10
5. POST /api/reports/html → Reporte HTML con formulario HITL integrado
6. Analista revisa → POST /api/feedback → auto-indexing si score >= 8.0
```

### Comandos curl paso a paso

```bash
# Paso 1: Obtener detalles de la transacción
curl -s http://localhost:8000/api/transactions/TXN-00042 | jq .

# Paso 2: Verificar historial del cliente (el estado VIP influye en la resolución)
curl -s http://localhost:8000/api/clients/CLI-0036/history | jq .

# Paso 3: Verificar perfil de riesgo del comercio
curl -s http://localhost:8000/api/merchants/Airbnb/risk | jq .

# Paso 4a: Investigación completa vía webhook n8n
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00042", "motivo": "Fraude con tarjeta", "cliente_vip": true}' \
  -o reporte_hitl.html

# Paso 4b (alternativa): Panel interactivo
#   http://localhost:8000/panel → TXN-00042 → Analizar

# Paso 4c (alternativa): Demo en vivo
#   https://ciri-chargeback-agent.onrender.com/panel

# Paso 5: Después de que el agente genera la resolución, se ejecuta el Juez automáticamente
# (incluido en el pipeline — este curl es solo para prueba aislada)
curl -s -X POST http://localhost:8000/api/analyze/judge \
  -H "Content-Type: application/json" \
  -d '{
    "full_context": {
      "transaction_id": "TXN-00042",
      "fraud_score": 4,
      "payment_method": "Crédito Visa",
      "country": "BRA",
      "cliente_vip": true
    },
    "resolution": {
      "transaction_id": "TXN-00042",
      "recommended_action": "PENDING_HITL",
      "confidence": 0.65,
      "risk_level": "HIGH",
      "policy_verdicts": [
        {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score 4/100 bajo umbral", "requires_human_review": true}
      ],
      "next_steps": ["Revisar con analista senior"]
    }
  }' | jq '{overall_score: .overall_score, approved: .approved, weaknesses: .weaknesses}'

# Paso 6: El analista revisa el reporte HTML y envía su decisión
curl -s -X POST http://localhost:8000/api/feedback/ \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "TXN-00042",
    "analyst_decision": "APPROVED",
    "analyst_notes": "Cliente VIP con historial limpio de 18 meses. Score bajo pero patrón de compra consistente. Riesgo aceptado por política de fidelización.",
    "final_outcome": "APPROVED",
    "judge_score": 8.7,
    "resolution": {
      "recommended_action": "PENDING_HITL",
      "justification": "Score 4/100 activa POL-FRD-001 FAIL. Sin BLOCKER — caso requiere evaluación humana dado el perfil VIP del cliente."
    }
  }' | jq .
```

### Salidas esperadas

**Resolución del agente (antes del Juez):**

```json
{
  "transaction_id": "TXN-00042",
  "recommended_action": "PENDING_HITL",
  "confidence": 0.65,
  "risk_level": "HIGH",
  "justification": "TXN-00042: score antifraude de 4/100 activa POL-FRD-001 como FAIL (alto riesgo). No hay BLOCKER — método Crédito Visa es reversible. Cliente VIP con historial positivo presenta tensión entre riesgo de fraude y política de retención de clientes premium. Caso requiere evaluación del analista.",
  "policy_verdicts": [
    {
      "policy_code": "POL-FRD-001",
      "verdict": "FAIL",
      "reasoning": "Score 4/100 inferior al umbral mínimo de 30. Alto riesgo de fraude.",
      "requires_human_review": true
    }
  ],
  "requires_hitl": true,
  "hitl_reason": "Fraud score crítico (4/100) con cliente VIP — decisión requiere juicio humano",
  "guardrail_warnings": []
}
```

**Evaluación del Juez:**

```json
{
  "overall_score": 8.7,
  "criteria": {
    "policy_consistency": 9.1,
    "justification_quality": 8.8,
    "precedent_usage": 8.6,
    "risk_assessment": 8.9,
    "actionability": 8.3
  },
  "approved": true,
  "strengths": [
    "Identificó correctamente el FAIL de POL-FRD-001 con dato específico (score=4)",
    "Escaló apropiadamente a HITL dado el perfil VIP contradictorio con el riesgo"
  ],
  "weaknesses": [
    "Podría haber citado precedentes específicos de clientes VIP con score bajo"
  ]
}
```

**Respuesta del feedback (después de revisión del analista):**

```json
{
  "status": "recorded",
  "feedback_id": 7,
  "auto_indexed": true,
  "needs_review": false,
  "judge_score": 8.7
}
```

### Observaciones clave

1. **Sin BLOCKER — HITL activado:** Crédito Visa es reversible, así que solo `FAIL` para POL-FRD-001 (score=4). El motor de resolución produce correctamente `PENDING_HITL`.
2. **Score del Juez 8.7/10** (`data/informes_demo/analisis_TXN-00042.json`)**:** Buena consistencia entre políticas citadas y acción recomendada. El escalamiento a HITL es apropiado dada la ambigüedad VIP vs. riesgo.
3. **El analista sobrescribe a APPROVE:** El estado VIP del cliente y su historial limpio justifican aceptar el riesgo. Esta es una decisión de juicio que el LLM correctamente delegó.
4. **`auto_indexed: true`:** El score del feedback de 8.7 supera el umbral de 8.0 (`JUDGE_AUTO_INDEX_THRESHOLD`). Este caso de excepción VIP ahora está indexado como precedente en Qdrant.
5. **Aprendizaje del sistema:** La próxima vez que un cliente VIP con fraud_score entre 1-10 presente un contracargo en Airbnb, este precedente aparecerá en los top-5 resultados y el agente propondrá una resolución más matizada.
6. **Alerta emitida:** El pipeline emite una alerta `hitl_required` (severidad WARN) via `POST /api/alerts/`, visible en el log de alertas del panel.

---

## Escenario 3: TXN-00089 — Débito Visa + USA → SLA Extendido (WARNING)

### Qué demuestra

La detección de políticas geográficas del sistema. Las transacciones de países fuera de LATAM (USA) están sujetas a un SLA extendido de 15 días hábiles (POL-EXC-004), comparado con los 10 días estándar para países LATAM (POL-SLA-002). El QueryBuilder enriquece automáticamente la query con `"internacional fuera LATAM plazo extendido"` para asegurar que estas políticas sean recuperadas y evaluadas. El escenario también muestra un veredicto WARNING (no BLOCKER) — el caso puede continuar pero el plazo extendido debe ser comunicado.

### Perfil de la transacción

| Campo | Valor |
|-------|-------|
| ID | TXN-00089 |
| Comercio | Booking |
| Monto | USD 889,02 |
| Método de pago | Débito Visa |
| País | USA |
| Fraud score | 8 / 100 |
| Canal | App Móvil |
| Cliente VIP | No |

### Flujo esperado del pipeline

```
1. Webhook/Panel recibe {"transaction_id": "TXN-00089"}
2. Recopilación de contexto (7 llamadas HTTP)
3. POST /api/sla/check → SLA extendido detectado (USA no está en LATAM_COUNTRIES)
   → deadline: 15 días hábiles (en vez de 10)
   → sla_type: "extended"
4. POST /api/analyze/resolve → LLM evalúa políticas (Haiku) + sintetiza (Sonnet)
   → POL-FRD-001: FAIL (score 8 < umbral 30)
   → POL-EXC-004: WARNING (país no-LATAM, SLA extendido)
   → POL-SLA-002: NOT_APPLICABLE (aplica solo a LATAM)
   → fraud_score < 15 (`RISK_FRAUD_SEVERE`) → riesgo HIGH
   → Acción: PENDING_HITL (score crítico + caso internacional)
5. POST /api/analyze/judge → Juez (Sonnet) → score ~8.7/10
6. POST /api/reports/html → Reporte HTML con badge WARNING amarillo
```

### Comandos curl paso a paso

```bash
# Paso 1: Obtener transacción y verificar país USA
curl -s http://localhost:8000/api/transactions/TXN-00089 \
  | jq '{id, merchant, country, payment_method, fraud_score}'

# Paso 2: Verificar estado SLA (mostrará deadline de 15 días extendidos)
curl -s -X POST http://localhost:8000/api/sla/check \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00089", "country": "USA", "cliente_vip": false}' | jq .

# Paso 3: Verificar que el QueryBuilder incluye enriquecimiento no-LATAM
curl -s "http://localhost:8000/api/policies/search?payment_method=Debito+Visa&fraud_score=8&country=USA" \
  | jq '{query: .query_used, retrieved_policies: [.results[] | .code]}'

# Paso 4a: Investigación completa vía webhook n8n
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00089", "motivo": "Servicio no entregado"}' \
  -o reporte_warning.html

# Paso 4b (alternativa): Panel interactivo
#   http://localhost:8000/panel → TXN-00089 → Analizar

# Paso 4c (alternativa): Demo en vivo
#   https://ciri-chargeback-agent.onrender.com/panel
```

### Salida esperada

**Búsqueda de políticas (paso 3) — enriquecimiento del QueryBuilder visible:**

```json
{
  "query": "contracargo Servicio no entregado, App Móvil, Débito Visa, score 8/100, USA transaccion de alto riesgo fraude score bajo internacional fuera LATAM plazo extendido",
  "retrieved_policies": [
    "POL-EXC-004",
    "POL-SLA-002",
    "POL-SLA-004",
    "POL-FRD-001",
    "POL-FRD-002",
    "POL-CB-001"
  ]
}
```

**Resolución (paso 4):**

```json
{
  "transaction_id": "TXN-00089",
  "recommended_action": "PENDING_HITL",
  "confidence": 0.72,
  "risk_level": "HIGH",
  "justification": "TXN-00089 presenta score antifraude de 8/100 (POL-FRD-001 FAIL — umbral 30). Transacción originada en USA activa POL-EXC-004 — plazo de resolución extendido a 15 días hábiles en lugar de los 10 días LATAM estándar. No hay BLOCKER (Débito Visa es reversible). El riesgo de fraude alto combinado con la complejidad de un caso internacional justifica revisión humana.",
  "policy_verdicts": [
    {
      "policy_code": "POL-FRD-001",
      "verdict": "FAIL",
      "reasoning": "Score antifraude 8/100, umbral mínimo 30. FAIL confirmado.",
      "requires_human_review": true
    },
    {
      "policy_code": "POL-EXC-004",
      "verdict": "WARNING",
      "reasoning": "País de origen: USA (fuera de LATAM). Aplica plazo extendido de 15 días hábiles según POL-EXC-004. El cliente debe ser notificado del plazo extendido.",
      "requires_human_review": false
    },
    {
      "policy_code": "POL-SLA-002",
      "verdict": "NOT_APPLICABLE",
      "reasoning": "POL-SLA-002 es el SLA estándar de 10 días para países LATAM. USA no está en LATAM — aplica POL-EXC-004 con 15 días.",
      "requires_human_review": false
    }
  ],
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "requires_hitl": true,
  "hitl_reason": "Score crítico (8/100) + caso internacional USA — requiere evaluación del analista",
  "next_steps": [
    "Notificar al cliente que el plazo de resolución es de 15 días hábiles (POL-EXC-004, no 10 días LATAM)",
    "Solicitar evidencia de no entrega del servicio al cliente dentro de 5 días",
    "Contactar a Booking para obtener prueba de entrega del servicio digital",
    "Escalar al equipo de fraude internacional si Booking no responde en 48h"
  ],
  "guardrail_warnings": []
}
```

### Observaciones clave

1. **QueryBuilder agregó dos enriquecimientos para USA + score=8:**
   - `"transaccion de alto riesgo fraude score bajo"` (fraud_score=8 < 30, umbral `FRAUD_SCORE_HIGH_RISK_THRESHOLD`)
   - `"internacional fuera LATAM plazo extendido"` (country=USA no pertenece al conjunto `LATAM_COUNTRIES`)
   Estos términos aseguraron que POL-EXC-004 fuera recuperada y rankeada con alta relevancia a pesar de que el corpus contiene 17 políticas.

2. **Distinción WARNING vs. BLOCKER:** POL-EXC-004 produce un `WARNING` (SLA extendido), no un `BLOCKER`. El caso puede continuar — el analista solo necesita comunicar el plazo de 15 días en vez de 10.

3. **Interacción entre SLAs:** POL-SLA-002 (SLA estándar de 10 días) se evalúa correctamente como `NOT_APPLICABLE` porque POL-EXC-004 la sobrescribe para transacciones fuera de LATAM. El LLM entiende esta jerarquía a partir de las descripciones de las políticas.

4. **Sin compensación aún:** El reloj del SLA comienza al abrir el caso, no en la fecha de la transacción. Si pasan 15 días hábiles sin resolución, POL-SLA-004 activaría `compensation_applicable=true` y una compensación de USD 15.

5. **HITL por fraud score:** Aunque no hay BLOCKER, el score de 8/100 activa POL-FRD-001 como `FAIL`, empujando el caso a `PENDING_HITL`. El analista debe evaluar el reclamo de "servicio no entregado" en el contexto del alto riesgo de fraude.

---

## Ejecución rápida de los tres escenarios

### Vía panel interactivo (recomendado)

Abrir el panel en el navegador:

```
# Local
http://localhost:8000/panel

# Producción (Render)
https://ciri-chargeback-agent.onrender.com/panel
```

Seleccionar modo **Directo (sin n8n)** para ver el progreso en tiempo real via SSE streaming. Probar en orden:
1. Ingresar `TXN-00051` → resultado: BLOCKER / REJECT (el streaming muestra cada paso con datos reales)
2. Ingresar `TXN-00042` → resultado: HIGH / PENDING_HITL
3. Ingresar `TXN-00089` → resultado: HIGH / PENDING_HITL con WARNING de SLA extendido

### Vía curl (automatizado)

```bash
#!/bin/bash
# Script de demo rápida — ejecuta los 3 escenarios en secuencia

echo "=== Escenario 1: TXN-00051 (Cripto BLOCKER) ==="
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  | jq '{action: .recommended_action, risk: .risk_level}'

echo ""
echo "=== Escenario 2: TXN-00042 (Crédito Visa HITL) ==="
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00042", "motivo": "Fraude con tarjeta", "cliente_vip": true}' \
  | jq '{action: .recommended_action, hitl: .requires_hitl}'

echo ""
echo "=== Escenario 3: TXN-00089 (Débito Visa USA WARNING) ==="
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00089", "motivo": "Servicio no entregado"}' \
  | jq '{
    action: .recommended_action,
    warnings: [.policy_verdicts[] | select(.verdict == "WARNING") | .policy_code]
  }'
```

### Resumen esperado

| TXN | Acción | Riesgo | HITL | Política clave | Score Juez |
|-----|--------|--------|------|----------------|------------|
| TXN-00051 | REJECT | BLOCKER | No | POL-EXC-003 (Cripto) + POL-FRD-001 | 8.6 |
| TXN-00042 | PENDING_HITL | HIGH | Sí | POL-FRD-001 (score=4) + VIP | 8.7 |
| TXN-00089 | PENDING_HITL | HIGH | Sí | POL-EXC-004 (USA, WARNING) + POL-FRD-001 | 8.7 |

### Nota sobre idempotencia y cache

La segunda ejecución de cualquier escenario se beneficia del cache de idempotencia (SQLite exact-match). El tiempo de respuesta baja de ~113s a ~2s en ejecuciones subsiguientes con los mismos parámetros. Esto es visible en el reporte HTML como "Cache hit: Sí".

El pipeline directo (panel de testing) también implementa cache check al inicio — si ya existe un reporte para la misma combinación `transaction_id|cliente_vip|motivo`, lo devuelve inmediatamente sin ejecutar el pipeline. El historial del panel guarda los reportes HTML generados; al hacer clic en un análisis del historial, se muestra el reporte directamente sin re-ejecutar.
