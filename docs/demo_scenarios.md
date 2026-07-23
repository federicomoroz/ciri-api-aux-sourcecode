# Escenarios de Demostración — Agente de Contracargos CIRI

Tres escenarios end-to-end que demuestran los comportamientos centrales del sistema: rechazo automático por BLOCKER, escalamiento HITL (Human-in-the-Loop) para casos ambiguos de alto riesgo, y detección de SLA extendido para transacciones fuera de LATAM.

---

## Requisitos previos

### Opción 1: Demo en vivo (sin instalación)

El panel interactivo está desplegado en Render:

```
https://ciri-chargeback-agent.onrender.com/panel
```

Simplemente ingresá un ID de transacción (ej. `TXN-00051`) y hacé clic en "Investigar". El pipeline completo se ejecuta contra la API en producción.

### Opción 2: Stack local

```bash
docker-compose up -d
python scripts/seed_data.py    # Excel → SQLite + Qdrant (ejecutar solo la primera vez)
```

El panel de pruebas está disponible en `http://localhost:8000/panel`.

### Panel de pruebas (`/panel`)

La forma más fácil de probar cualquier escenario es el panel interactivo en `/panel`. Funciona con o sin n8n — si n8n no está disponible, el panel ejecuta el pipeline completo directamente contra la API de FastAPI como fallback. Solo se necesita ingresar el `transaction_id` y opcionalmente el motivo del contracargo.

---

## Modelo dual de LLM

El sistema utiliza una estrategia de dos modelos para optimizar costo y calidad:

| Etapa | Modelo | Justificación |
|-------|--------|---------------|
| Evaluación de políticas (call 1) | `claude-haiku-4-5` | Rápido y económico — evalúa 17 políticas contra datos estructurados |
| Síntesis de resolución (call 2) | `claude-sonnet-4` | Mayor capacidad de razonamiento para generar justificaciones citadas |
| Juez de calidad (call 3) | `claude-sonnet-4` | Evaluación crítica — requiere juicio calibrado (5 criterios, score 1-10) |

El score promedio del Juez en los escenarios de prueba es **9.1/10**, lo que indica alta consistencia entre la evaluación de políticas y la síntesis final.

---

## Escenario 1: TXN-00051 — Cripto + Fraude → Rechazo Automático (BLOCKER)

### Qué demuestra

La capacidad del sistema para aplicar exclusiones de política no negociables. Las transacciones con criptomonedas son irreversibles por definición (POL-EXC-003). Combinado con un fraud_score de 8/100 (POL-FRD-001, umbral mínimo 15), este caso produce dos veredictos BLOCKER. La resolución debe ser `REJECT` sin importar cualquier otra evidencia. Este escenario también muestra el guardrail: si el LLM alucinara un `APPROVE`, el sistema lo corrige automáticamente.

### Perfil de la transacción

| Campo | Valor |
|-------|-------|
| ID | TXN-00051 |
| Comercio | CryptoVault SA |
| Monto | USD 850.00 |
| Método de pago | Cripto |
| País | ARG |
| Fraud score | 8 / 100 |
| Canal | Web |
| Cliente VIP | No |

### Flujo esperado del pipeline

```
1. Webhook/Panel recibe {"transaction_id": "TXN-00051"}
2. GET /api/transactions/TXN-00051        → datos de la transacción
3. GET /api/logs/TXN-00051                → logs asociados
4. GET /api/policies/search               → RAG semántico → recupera 17 políticas
5. GET /api/cases/similar                 → RAG semántico → precedentes similares
6. GET /api/merchants/CryptoVault+SA/risk → perfil de riesgo del comercio
7. GET /api/clients/{id}/history          → historial del cliente
8. POST /api/sla/check                    → verificación SLA (10 días LATAM)
9. POST /api/analyze/resolve              → LLM evalúa políticas (Haiku) + sintetiza (Sonnet)
   → POL-EXC-003: BLOCKER (cripto irreversible)
   → POL-FRD-001: BLOCKER (score 8 < umbral 15)
   → Acción determinística: REJECT (hay BLOCKERs)
10. POST /api/analyze/judge               → LLM-as-Judge (Sonnet) → score ~9.2/10
11. POST /api/reports/html                → Reporte HTML con badge BLOCKER rojo
```

### Comandos curl paso a paso

```bash
# Paso 1: Verificar que la transacción existe
curl -s http://localhost:8000/api/transactions/TXN-00051 | jq .

# Paso 2: Ver qué políticas recupera el RAG (el QueryBuilder enriquece la query automáticamente)
curl -s "http://localhost:8000/api/policies/search?payment_method=Cripto&fraud_score=8&country=ARG" \
  | jq '{query: .query_used, count: .count}'

# Paso 3: Buscar precedentes similares
curl -s "http://localhost:8000/api/cases/similar?merchant=CryptoVault+SA&amount_usd=850&payment_method=Cripto&country=ARG&fraud_score=8" \
  | jq '.results[] | {case_id, resolution, fraud_score}'

# Paso 4a: Investigación completa vía webhook n8n (ruta principal)
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051", "motivo": "No reconoce la compra"}' \
  -o reporte_blocker.html

# Paso 4b (alternativa): Panel interactivo — abrir en el navegador:
#   http://localhost:8000/panel
#   Ingresar TXN-00051 → clic en "Investigar"

# Paso 4c (alternativa): Demo en vivo en Render:
#   https://ciri-chargeback-agent.onrender.com/panel
```

### Salida esperada

```json
{
  "transaction_id": "TXN-00051",
  "recommended_action": "REJECT",
  "confidence": 0.97,
  "risk_level": "BLOCKER",
  "justification": "TXN-00051 involucra una transacción con método de pago Cripto. POL-EXC-003 establece que las criptomonedas son irreversibles por naturaleza — BLOCKER obligatorio. Adicionalmente, el score antifraude de 8/100 activa POL-FRD-001 (umbral mínimo 15) como BLOCKER secundario. La combinación de dos BLOCKERs hace que el rechazo sea mandatorio.",
  "policy_verdicts": [
    {
      "policy_code": "POL-EXC-003",
      "verdict": "BLOCKER",
      "reasoning": "Método de pago: Cripto. Transacciones con criptomonedas son irreversibles. BLOCKER sin excepción.",
      "requires_human_review": false
    },
    {
      "policy_code": "POL-FRD-001",
      "verdict": "BLOCKER",
      "reasoning": "Score antifraude: 8/100, umbral mínimo: 15. Alto riesgo de fraude confirmado.",
      "requires_human_review": false
    }
  ],
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "requires_hitl": false,
  "next_steps": [
    "Notificar al cliente el rechazo del contracargo citando POL-EXC-003 y POL-FRD-001",
    "Reportar transacción TXN-00051 al equipo de fraude para investigación",
    "Revisar perfil de CryptoVault SA — evaluar suspensión si tasa de fraude supera el 2%",
    "Archivar caso con clasificación FRAUDE_CONFIRMADO_CRIPTO"
  ],
  "guardrail_warnings": []
}
```

### Observaciones clave

1. **Dos BLOCKERs detectados:** POL-EXC-003 (Cripto = irreversible) y POL-FRD-001 (score=8 < 15). La acción `REJECT` se determina de forma determinística antes de que el LLM sintetice la justificación.
2. **Guardrail no activado:** El LLM produce correctamente `REJECT` — el guardrail no tiene nada que corregir. Si hubiera dicho `APPROVE`, el sistema lo habría sobrescrito.
3. **Sin HITL:** Los casos BLOCKER son determinísticos — la revisión del analista no agrega valor.
4. **Sin compensación:** El SLA no fue incumplido (el caso se rechazó inmediatamente).
5. **Score del Juez esperado:** ~9.2/10 — alta puntuación porque la resolución cita correctamente ambas políticas con datos específicos y la acción es consistente con los veredictos.

---

## Escenario 2: TXN-00042 — Crédito Visa + Score Bajo + VIP → HITL (Revisión Analista)

### Qué demuestra

La ruta de escalamiento Human-in-the-Loop del sistema. Cuando el fraud_score=4 indica alto riesgo y hay veredictos FAIL pero no BLOCKER (Crédito Visa es reversible), la resolución no puede ser auto-aprobada ni auto-rechazada. El caso requiere juicio humano. Este escenario muestra cómo el sistema gestiona la tensión entre riesgo de fraude y política de retención de clientes VIP, delegando correctamente al analista.

### Perfil de la transacción

| Campo | Valor |
|-------|-------|
| ID | TXN-00042 |
| Comercio | TechStore AR |
| Monto | USD 234.50 |
| Método de pago | Credito Visa |
| País | ARG |
| Fraud score | 4 / 100 |
| Canal | Web |
| Cliente VIP | Sí |

### Flujo esperado del pipeline

```
1. Webhook/Panel recibe {"transaction_id": "TXN-00042", "cliente_vip": true}
2. Recopilación de contexto (7 llamadas HTTP en paralelo)
3. POST /api/analyze/resolve → LLM evalúa políticas (Haiku) + sintetiza (Sonnet)
   → POL-FRD-001: FAIL (score 4 < umbral 15, pero NO es BLOCKER — Visa es reversible)
   → Sin BLOCKERs → acción no determinística
   → fraud_score < 15 → riesgo HIGH
   → Acción: PENDING_HITL (score crítico + VIP = requiere juicio humano)
4. POST /api/analyze/judge → Juez (Sonnet) → score ~9.0/10
5. POST /api/reports/html → Reporte HTML con formulario HITL integrado
6. Analista revisa → POST /api/feedback → auto-indexing si score >= 8.0
```

### Comandos curl paso a paso

```bash
# Paso 1: Obtener detalles de la transacción
curl -s http://localhost:8000/api/transactions/TXN-00042 | jq .

# Paso 2: Verificar historial del cliente (el estado VIP influye en la resolución)
curl -s http://localhost:8000/api/clients/CLI-0042/history | jq .

# Paso 3: Verificar perfil de riesgo del comercio
curl -s http://localhost:8000/api/merchants/TechStore+AR/risk | jq .

# Paso 4a: Investigación completa vía webhook n8n
curl -s -X POST http://localhost:5678/webhook/chargeback-agent \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00042", "motivo": "Fraude con tarjeta", "cliente_vip": true}' \
  -o reporte_hitl.html

# Paso 4b (alternativa): Panel interactivo
#   http://localhost:8000/panel → TXN-00042 → Investigar

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
      "payment_method": "Credito Visa",
      "country": "ARG",
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
    "judge_score": 9.0,
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
      "reasoning": "Score 4/100 inferior al umbral mínimo de 15. Alto riesgo de fraude.",
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
  "overall_score": 9.0,
  "criteria": {
    "policy_consistency": 9.5,
    "justification_quality": 9.0,
    "precedent_usage": 8.5,
    "risk_assessment": 9.0,
    "actionability": 9.5
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
  "judge_score": 9.0
}
```

### Observaciones clave

1. **Sin BLOCKER — HITL activado:** Crédito Visa es reversible, así que solo `FAIL` para POL-FRD-001 (score=4). El motor de resolución produce correctamente `PENDING_HITL`.
2. **Score del Juez ~9.0/10:** Buena consistencia entre políticas citadas y acción recomendada. El escalamiento a HITL es apropiado dada la ambigüedad VIP vs. riesgo.
3. **El analista sobrescribe a APPROVE:** El estado VIP del cliente y su historial limpio justifican aceptar el riesgo. Esta es una decisión de juicio que el LLM correctamente delegó.
4. **`auto_indexed: true`:** El score del feedback de 9.0 supera el umbral de 8.0 (`JUDGE_AUTO_INDEX_THRESHOLD`). Este caso de excepción VIP ahora está indexado como precedente en Qdrant.
5. **Aprendizaje del sistema:** La próxima vez que un cliente VIP con fraud_score entre 1-10 presente un contracargo en TechStore AR, este precedente aparecerá en los top-5 resultados y el agente propondrá una resolución más matizada.

---

## Escenario 3: TXN-00089 — Débito Visa + USA → SLA Extendido (WARNING)

### Qué demuestra

La detección de políticas geográficas del sistema. Las transacciones de países fuera de LATAM (USA) están sujetas a un SLA extendido de 15 días hábiles (POL-EXC-004), comparado con los 10 días estándar para países LATAM (POL-SLA-002). El QueryBuilder enriquece automáticamente la query con `"internacional fuera LATAM plazo extendido"` para asegurar que estas políticas sean recuperadas y evaluadas. El escenario también muestra un veredicto WARNING (no BLOCKER) — el caso puede continuar pero el plazo extendido debe ser comunicado.

### Perfil de la transacción

| Campo | Valor |
|-------|-------|
| ID | TXN-00089 |
| Comercio | GameZone Pro |
| Monto | USD 129.99 |
| Método de pago | Debito Visa |
| País | USA |
| Fraud score | 8 / 100 |
| Canal | Web |
| Cliente VIP | No |

### Flujo esperado del pipeline

```
1. Webhook/Panel recibe {"transaction_id": "TXN-00089"}
2. Recopilación de contexto (7 llamadas HTTP)
3. POST /api/sla/check → SLA extendido detectado (USA no está en LATAM_COUNTRIES)
   → deadline: 15 días hábiles (en vez de 10)
   → sla_type: "extended"
4. POST /api/analyze/resolve → LLM evalúa políticas (Haiku) + sintetiza (Sonnet)
   → POL-FRD-001: FAIL (score 8 < umbral 15)
   → POL-EXC-004: WARNING (país no-LATAM, SLA extendido)
   → POL-SLA-002: NOT_APPLICABLE (aplica solo a LATAM)
   → fraud_score < 15 → riesgo HIGH
   → Acción: PENDING_HITL (score crítico + caso internacional)
5. POST /api/analyze/judge → Juez (Sonnet) → score ~9.1/10
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
#   http://localhost:8000/panel → TXN-00089 → Investigar

# Paso 4c (alternativa): Demo en vivo
#   https://ciri-chargeback-agent.onrender.com/panel
```

### Salida esperada

**Búsqueda de políticas (paso 3) — enriquecimiento del QueryBuilder visible:**

```json
{
  "query": "contracargo Servicio no entregado, Web, Debito Visa, score 8/100, USA transaccion de alto riesgo fraude score bajo internacional fuera LATAM plazo extendido",
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
  "justification": "TXN-00089 presenta score antifraude de 8/100 (POL-FRD-001 FAIL — umbral 15). Transacción originada en USA activa POL-EXC-004 — plazo de resolución extendido a 15 días hábiles en lugar de los 10 días LATAM estándar. No hay BLOCKER (Débito Visa es reversible). El riesgo de fraude alto combinado con la complejidad de un caso internacional justifica revisión humana.",
  "policy_verdicts": [
    {
      "policy_code": "POL-FRD-001",
      "verdict": "FAIL",
      "reasoning": "Score antifraude 8/100, umbral mínimo 15. FAIL confirmado.",
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
    "Contactar a GameZone Pro para obtener prueba de entrega del servicio digital",
    "Escalar al equipo de fraude internacional si GameZone Pro no responde en 48h"
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

Probar en orden:
1. Ingresar `TXN-00051` → resultado: BLOCKER / REJECT
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
| TXN-00051 | REJECT | BLOCKER | No | POL-EXC-003 (Cripto) + POL-FRD-001 | ~9.2 |
| TXN-00042 | PENDING_HITL | HIGH | Sí | POL-FRD-001 (score=4) + VIP | ~9.0 |
| TXN-00089 | PENDING_HITL | HIGH | Sí | POL-EXC-004 (USA, WARNING) + POL-FRD-001 | ~9.1 |

### Nota sobre idempotencia

La segunda ejecución de cualquier escenario se beneficia del cache de idempotencia (SQLite exact-match). El tiempo de respuesta baja de ~113s a ~2s en ejecuciones subsiguientes con los mismos parámetros. Esto es visible en el reporte HTML como "Cache hit: true".
