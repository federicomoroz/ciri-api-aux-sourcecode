# Demo Scenarios — CIRI Chargeback Agent

Three end-to-end scenarios that demonstrate the core behaviors of the system: hard BLOCKER enforcement, HITL escalation for ambiguous high-risk cases, and extended SLA detection for non-LATAM transactions.

Run all scenarios against the local stack:
```bash
docker-compose up -d
docker-compose exec api python -m app.seed_data
```

---

## Scenario 1: TXN-00051 — Cripto Fraud → Auto-Reject (BLOCKER)

### What this demonstrates

The system's ability to enforce non-negotiable policy exclusions. Cripto transactions are irreversible by definition (POL-EXC-003). Combined with a fraud score of 8/100 (POL-FRD-001), this case produces two BLOCKER verdicts. The resolution must be `REJECT` regardless of any other evidence. This scenario also shows the guardrail: if the LLM were to hallucinate an `APPROVE`, the system auto-corrects it.

### Transaction profile

| Field | Value |
|---|---|
| ID | TXN-00051 |
| Merchant | CryptoVault SA |
| Amount | USD 850.00 |
| Payment method | Cripto |
| Country | ARG |
| Fraud score | 8 / 100 |
| Channel | Web |
| Client VIP | No |

### Step-by-step curl commands

```bash
# Step 1: Confirm transaction exists
curl -s http://localhost:8000/api/transactions/TXN-00051 | jq .

# Step 2: Check what policies will be retrieved (QueryBuilder enrichment visible in _query field)
curl -s "http://localhost:8000/api/policies/search?payment_method=Cripto&fraud_score=8&country=ARG" \
  | jq '{query: .query_used, count: .count}'

# Step 3: Find similar precedents
curl -s "http://localhost:8000/api/cases/similar?merchant=CryptoVault+SA&amount_usd=850&payment_method=Cripto&country=ARG&fraud_score=8" \
  | jq '.results[] | {case_id, resolution, fraud_score}'

# Step 4: Full resolution via n8n webhook (primary path)
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051"}' | jq .

# Step 4 (alternative): Direct FastAPI call bypassing n8n
curl -s -X POST http://localhost:8000/api/analyze/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "tx_data": {
      "id": "TXN-00051",
      "merchant": "CryptoVault SA",
      "amount_usd": 850.00,
      "payment_method": "Cripto",
      "country": "ARG",
      "fraud_score": 8,
      "channel": "Web",
      "cliente_vip": false
    },
    "policies": [],
    "similar_cases": [],
    "logs": [],
    "merchant_risk": {},
    "client_history": {},
    "motivo": "Cargo no reconocido",
    "cliente_vip": false
  }' | jq '{
    action: .recommended_action,
    risk: .risk_level,
    confidence: .confidence,
    guardrails: .guardrail_warnings,
    blockers: [.policy_verdicts[] | select(.verdict == "BLOCKER") | .policy_code]
  }'
```

### Expected Output

```json
{
  "transaction_id": "TXN-00051",
  "recommended_action": "REJECT",
  "confidence": 0.97,
  "risk_level": "BLOCKER",
  "justification": "TXN-00051 involucra una transaccion con metodo de pago Cripto. POL-EXC-003 establece que las criptomonedas son irreversibles por naturaleza — BLOCKER obligatorio. Adicionalmente, el score antifraude de 8/100 activa POL-FRD-001 (umbral minimo 15) como BLOCKER secundario. La combinacion de dos BLOCKERs hace que el rechazo sea mandatorio.",
  "policy_verdicts": [
    {
      "policy_code": "POL-EXC-003",
      "verdict": "BLOCKER",
      "reasoning": "Metodo de pago: Cripto. Transacciones con criptomonedas son irreversibles. BLOCKER sin excepcion.",
      "requires_human_review": false
    },
    {
      "policy_code": "POL-FRD-001",
      "verdict": "BLOCKER",
      "reasoning": "Score antifraude: 8/100, umbral minimo: 15. Alto riesgo de fraude confirmado.",
      "requires_human_review": false
    }
  ],
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "requires_hitl": false,
  "next_steps": [
    "Notificar al cliente el rechazo del contracargo citando POL-EXC-003 y POL-FRD-001",
    "Reportar transaccion TXN-00051 al equipo de fraude para investigacion",
    "Revisar perfil de CryptoVault SA — evaluar suspension si tasa de fraude supera el 2%",
    "Archivar caso con clasificacion FRAUDE_CONFIRMADO_CRIPTO"
  ],
  "guardrail_warnings": []
}
```

### Key observations

1. **Two BLOCKERs detected:** POL-EXC-003 (Cripto = irreversible) and POL-FRD-001 (score=8 < 15)
2. **No guardrail triggered:** The LLM correctly produces `REJECT` — guardrail has nothing to correct
3. **No HITL required:** BLOCKER cases are deterministic — analyst review adds no value here
4. **No compensation:** SLA was not breached (the case was rejected immediately)
5. **QueryBuilder enrichment for this case:**
   - Appended: `"criptomonedas no reversible blocker"` (payment_method == "Cripto")
   - Appended: `"transaccion de alto riesgo fraude score bajo"` (fraud_score=8 < 30)

---

## Scenario 2: TXN-00042 — Credit Visa + Low Score → HITL (Analyst Review)

### What this demonstrates

The system's Human-in-the-Loop escalation path. When fraud_score=4 is high-risk and there are FAIL verdicts but no BLOCKER (Visa credit is reversible), the resolution cannot be auto-approved or auto-rejected. The case requires analyst judgment. This scenario shows the Judge scoring below the auto-approve threshold and the system correctly routing to the HITL branch in n8n.

### Transaction profile

| Field | Value |
|---|---|
| ID | TXN-00042 |
| Merchant | TechStore AR |
| Amount | USD 234.50 |
| Payment method | Credito Visa |
| Country | ARG |
| Fraud score | 4 / 100 |
| Channel | Web |
| Client VIP | Yes |

### Step-by-step curl commands

```bash
# Step 1: Get transaction details
curl -s http://localhost:8000/api/transactions/TXN-00042 | jq .

# Step 2: Check client history (VIP status influences resolution)
curl -s http://localhost:8000/api/transactions/TXN-00042/client-history | jq .

# Step 3: Check merchant risk profile
curl -s http://localhost:8000/api/merchants/TechStore+AR/risk | jq .

# Step 4: Trigger investigation via n8n
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00042"}' | jq .

# Step 5: After agent generates resolution, run the Judge
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

# Step 6: Analyst submits feedback after reviewing HTML report
curl -s -X POST http://localhost:8000/api/feedback/ \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "TXN-00042",
    "analyst_decision": "APPROVED",
    "analyst_notes": "Cliente VIP con historial limpio de 18 meses. Score bajo pero patron de compra consistente. Riesgo aceptado por politica de fidelizacion.",
    "final_outcome": "APPROVED",
    "judge_score": 8.3,
    "resolution": {
      "recommended_action": "PENDING_HITL",
      "justification": "Score 4/100 activa POL-FRD-001 FAIL. Sin BLOCKER — caso requiere evaluacion humana dado el perfil VIP del cliente."
    }
  }' | jq .
```

### Expected Outputs

**Agent resolution (before Judge):**
```json
{
  "transaction_id": "TXN-00042",
  "recommended_action": "PENDING_HITL",
  "confidence": 0.65,
  "risk_level": "HIGH",
  "justification": "TXN-00042: score antifraude de 4/100 activa POL-FRD-001 como FAIL (alto riesgo). No hay BLOCKER — metodo Credito Visa es reversible. Cliente VIP con historial positivo presenta tension entre riesgo de fraude y politica de retencion de clientes premium. Caso requiere evaluacion del analista.",
  "policy_verdicts": [
    {
      "policy_code": "POL-FRD-001",
      "verdict": "FAIL",
      "reasoning": "Score 4/100 inferior al umbral minimo de 15. Alto riesgo de fraude.",
      "requires_human_review": true
    }
  ],
  "requires_hitl": true,
  "hitl_reason": "Fraude score critico (4/100) con cliente VIP — decision requiere juicio humano",
  "guardrail_warnings": []
}
```

**Judge evaluation:**
```json
{
  "overall_score": 6.8,
  "criteria": {
    "policy_consistency": 8.0,
    "justification_quality": 7.5,
    "precedent_usage": 5.0,
    "risk_assessment": 7.0,
    "actionability": 5.5
  },
  "approved": false,
  "strengths": [
    "Correctamente identifico el FAIL de POL-FRD-001 con dato especifico (score=4)",
    "Apropiadamente escalo a HITL dado el perfil VIP contradictorio"
  ],
  "weaknesses": [
    "No cito precedentes de clientes VIP con score bajo resueltos anteriormente",
    "Los next_steps son vagos — 'Revisar con analista senior' no es accionable"
  ]
}
```

**Feedback response (after analyst review):**
```json
{
  "status": "recorded",
  "feedback_id": 7,
  "auto_indexed": true,
  "needs_review": false,
  "judge_score": 8.3
}
```

### Key observations

1. **No BLOCKER — HITL triggered:** Visa credit is reversible, so only `FAIL` for POL-FRD-001 (score=4). The resolution engine correctly produces `PENDING_HITL`
2. **Judge score 6.8 < 7.0:** Poor precedent usage and vague next_steps push it below threshold. n8n routes to analyst
3. **Analyst overrides to APPROVE:** The client's VIP status and clean history justify accepting the risk. This is a judgment call that the LLM correctly deferred
4. **`auto_indexed: true`:** The analyst's feedback score of 8.3 exceeds the 8.0 threshold. This VIP exception case is now indexed as a precedent — future similar VIP cases with low scores will find this example
5. **Learning outcome:** Next time a VIP client with fraud_score between 1–10 files a chargeback at TechStore AR, this precedent will appear in the top-5 results and the agent will propose a more nuanced resolution

---

## Scenario 3: TXN-00089 — Debit Visa + USA → Extended SLA Warning

### What this demonstrates

The system's geographic policy detection. Transactions from non-LATAM countries (USA) are subject to extended SLA (15 business days per POL-EXC-004), compared to the standard 10 days in LATAM (POL-SLA-002). The QueryBuilder automatically enriches the query with `"internacional fuera LATAM plazo extendido"` to ensure these policies are retrieved and evaluated. The scenario also shows a WARNING verdict (not a BLOCKER) — the case can proceed but the extended timeline must be communicated.

### Transaction profile

| Field | Value |
|---|---|
| ID | TXN-00089 |
| Merchant | GameZone Pro |
| Amount | USD 129.99 |
| Payment method | Debito Visa |
| Country | USA |
| Fraud score | 8 / 100 |
| Channel | Web |
| Client VIP | No |

### Step-by-step curl commands

```bash
# Step 1: Get transaction and verify USA country
curl -s http://localhost:8000/api/transactions/TXN-00089 | jq '{id, merchant, country, payment_method, fraud_score}'

# Step 2: Check SLA status (will show extended 15-day deadline)
curl -s http://localhost:8000/api/sla/TXN-00089 | jq .

# Step 3: Verify QueryBuilder includes non-LATAM enrichment
curl -s "http://localhost:8000/api/policies/search?payment_method=Debito+Visa&fraud_score=8&country=USA" \
  | jq '{query: .query_used, retrieved_policies: [.results[] | .code]}'

# Step 4: Run full investigation via n8n
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00089"}' | jq .

# Step 5: Direct resolve call (showing WARNING handling)
curl -s -X POST http://localhost:8000/api/analyze/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "tx_data": {
      "id": "TXN-00089",
      "merchant": "GameZone Pro",
      "amount_usd": 129.99,
      "payment_method": "Debito Visa",
      "country": "USA",
      "fraud_score": 8,
      "channel": "Web",
      "cliente_vip": false
    },
    "policies": [],
    "similar_cases": [],
    "logs": [],
    "merchant_risk": {},
    "client_history": {},
    "motivo": "Servicio no entregado",
    "cliente_vip": false
  }' | jq '{
    action: .recommended_action,
    risk: .risk_level,
    warnings: [.policy_verdicts[] | select(.verdict == "WARNING") | {code: .policy_code, reason: .reasoning}],
    guardrails: .guardrail_warnings
  }'

# Step 6: Get HTML report
curl -s http://localhost:8000/api/reports/TXN-00089 \
  -H "Accept: text/html" -o /tmp/report_TXN-00089.html
echo "Report saved to /tmp/report_TXN-00089.html"
```

### Expected Output

**Policy search query (step 3) — QueryBuilder enrichment visible:**
```json
{
  "query": "contracargo Servicio no entregado, Web, Debito Visa, score 8/100, USA transaccion de alto riesgo fraude score bajo internacional fuera LATAM plazo extendido",
  "retrieved_policies": [
    "POL-EXC-004",
    "POL-SLA-002",
    "POL-SLA-004",
    "POL-FRD-001",
    "POL-FRD-002",
    "POL-CB-001",
    "POL-CB-002",
    "POL-CB-003",
    "POL-CB-004",
    "POL-CB-005",
    "POL-EXC-001",
    "POL-EXC-002",
    "POL-EXC-003",
    "POL-SLA-001",
    "POL-SLA-003",
    "POL-FRD-003",
    "POL-FRD-004"
  ]
}
```

**Resolution output (step 5):**
```json
{
  "transaction_id": "TXN-00089",
  "recommended_action": "PENDING_HITL",
  "confidence": 0.72,
  "risk_level": "HIGH",
  "justification": "TXN-00089 presenta score antifraude de 8/100 (POL-FRD-001 FAIL — umbral 15). Transaccion originada en USA activa POL-EXC-004 — plazo de resolucion extendido a 15 dias habiles en lugar de los 10 dias LATAM estandar. No hay BLOCKER (Debito Visa es reversible). El riesgo de fraude alto combinado con la complejidad de un caso internacional justifica revision humana.",
  "policy_verdicts": [
    {
      "policy_code": "POL-FRD-001",
      "verdict": "FAIL",
      "reasoning": "Score antifraude 8/100, umbral minimo 15. FAIL confirmado.",
      "requires_human_review": true
    },
    {
      "policy_code": "POL-EXC-004",
      "verdict": "WARNING",
      "reasoning": "Pais de origen: USA (fuera de LATAM). Aplica plazo extendido de 15 dias habiles segun POL-EXC-004. El cliente debe ser notificado del plazo extendido.",
      "requires_human_review": false
    },
    {
      "policy_code": "POL-SLA-002",
      "verdict": "NOT_APPLICABLE",
      "reasoning": "POL-SLA-002 es el SLA estandar de 10 dias para paises LATAM. USA no esta en LATAM — aplica POL-EXC-004 con 15 dias.",
      "requires_human_review": false
    }
  ],
  "risk_level": "HIGH",
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "requires_hitl": true,
  "hitl_reason": "Score critico (8/100) + caso internacional USA — requiere evaluacion del analista",
  "next_steps": [
    "Notificar al cliente que el plazo de resolucion es de 15 dias habiles (POL-EXC-004, no 10 dias LATAM)",
    "Solicitar evidencia de no entrega del servicio al cliente dentro de 5 dias",
    "Contactar a GameZone Pro para obtener prueba de entrega del servicio digital",
    "Escalar al equipo de fraude internacional si GameZone Pro no responde en 48h"
  ],
  "guardrail_warnings": []
}
```

### Key observations

1. **QueryBuilder appended two enrichments for USA + score=8:**
   - `"transaccion de alto riesgo fraude score bajo"` (fraud_score=8 < 30)
   - `"internacional fuera LATAM plazo extendido"` (country=USA not in LATAM set)
   These terms ensured POL-EXC-004 was retrieved and ranked highly despite the corpus containing 17 policies

2. **WARNING vs. BLOCKER distinction:** POL-EXC-004 produces a `WARNING` (extended SLA) rather than a `BLOCKER`. The case can proceed — the analyst just needs to communicate the 15-day timeline instead of 10 days

3. **SLA interaction:** POL-SLA-002 (10-day standard SLA) is correctly evaluated as `NOT_APPLICABLE` because POL-EXC-004 overrides it for non-LATAM transactions. The LLM understands this hierarchy from the policy descriptions

4. **No compensation yet:** The SLA clock starts at case opening, not at transaction date. If 15 business days elapse without resolution, POL-SLA-004 would trigger `compensation_applicable=true` and USD 15 compensation

5. **HITL for fraud score:** Even with no BLOCKER, the score of 8/100 activates POL-FRD-001 as `FAIL`, pushing the case to `PENDING_HITL`. The analyst must evaluate the "service not delivered" claim in the context of the high fraud risk

---

## Running All Three Scenarios in Sequence

```bash
#!/bin/bash
# Quick demo runner

echo "=== Scenario 1: TXN-00051 (Cripto BLOCKER) ==="
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00051"}' | jq '{action: .recommended_action, risk: .risk_level}'

echo ""
echo "=== Scenario 2: TXN-00042 (Credito Visa HITL) ==="
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00042"}' | jq '{action: .recommended_action, hitl: .requires_hitl}'

echo ""
echo "=== Scenario 3: TXN-00089 (Debito Visa USA WARNING) ==="
curl -s -X POST http://localhost:5678/webhook/chargeback \
  -H "Content-Type: application/json" \
  -d '{"transaction_id": "TXN-00089"}' | jq '{
    action: .recommended_action,
    warnings: [.policy_verdicts[] | select(.verdict == "WARNING") | .policy_code]
  }'
```

### Expected summary

| TXN | Action | Risk | HITL | Key policy |
|---|---|---|---|---|
| TXN-00051 | REJECT | BLOCKER | No | POL-EXC-003 (Cripto) + POL-FRD-001 |
| TXN-00042 | PENDING_HITL | HIGH | Yes | POL-FRD-001 (score=4) |
| TXN-00089 | PENDING_HITL | HIGH | Yes | POL-EXC-004 (USA, WARNING) + POL-FRD-001 |
