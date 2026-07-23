# Prompts — CIRI Chargeback Agent

Prompts are stored as versioned Python modules under `api/app/llm/prompts/`. Each module exports `SYSTEM`, `USER_TEMPLATE`, and a `render()` function that returns `(system_prompt, user_prompt)` as a tuple.

**Exception — v1_judge:** The Judge prompt is inlined directly in the `[Juez de Calidad]` HTTP Request node in n8n. This avoids a FastAPI proxy hop and guarantees the Judge always uses Claude (not n8n's built-in LLM node). The Python file `v1_judge.py` is the canonical source; the n8n node body parameter mirrors it.

---

## Version History

| Version | Date | Summary |
|---|---|---|
| v1.0 | 2025-01 | Initial release — 3 LLM prompts + deterministic log analysis for full resolution pipeline |
| v1.1 | 2025-07 | v1_resolution: deeper precedent analysis, contradiction resolution, provisional determination flagging |

---

## Prompt 1: v1_policy_eval

**File:** `api/app/llm/prompts/v1_policy_eval.py`

### Purpose

Evaluate a transaction against every retrieved policy and produce a structured verdict for each one. This is the first LLM call in the `/api/analyze/resolve` pipeline and determines which policies are violated, which are passed, and whether any hard blockers exist.

### Role

Auditor de cumplimiento (compliance auditor) for a Latin American fintech.

### Input Specification

| Parameter | Type | Description |
|---|---|---|
| `transaction` | `dict` | Full transaction record (id, amount, merchant, country, payment_method, fraud_score, channel, etc.) |
| `policies_text` | `str` | Formatted policy list retrieved from Qdrant via QueryBuilder |
| `policy_count` | `int` | Number of policies being evaluated |

### Output Specification

JSON array of `PolicyVerdict` objects:

```json
[
  {
    "policy_code": "POL-XXX-NNN",
    "verdict": "PASS | FAIL | BLOCKER | WARNING | NOT_APPLICABLE",
    "reasoning": "Concise explanation citing specific transaction data",
    "requires_human_review": false
  }
]
```

**Verdict definitions:**

| Verdict | Meaning |
|---|---|
| `PASS` | Transaction complies with this policy |
| `FAIL` | Transaction violates this policy |
| `BLOCKER` | Critical violation — chargeback CANNOT proceed under any circumstance |
| `WARNING` | Potential risk that requires attention but does not block processing |
| `NOT_APPLICABLE` | Policy is not relevant to this specific transaction |

### Strict Rules Embedded in Prompt

1. Be precise — cite specific transaction values (score=X, amount=USD Y, channel=Z)
2. `POL-EXC-003` applies ALWAYS as `BLOCKER` when payment method is "Cripto"
3. `POL-FRD-001` applies as `FAIL` or `BLOCKER` when anti-fraud score is below threshold
4. A `BLOCKER` verdict means the final resolution MUST reject the chargeback
5. Evaluate ALL provided policies — do not skip any
6. Respond ONLY with a valid JSON array — no additional text, no markdown fences

### Example Rendered User Prompt (abbreviated)

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

## POLITICAS A EVALUAR (recuperadas por RAG — 17 politicas)
**POL-EXC-003** — EXCEPCION
Nombre: Exclusion de criptomonedas
Descripcion: Las transacciones realizadas con criptomonedas son irreversibles...
Referencia: Reg. Fintech 2024/03

**POL-FRD-001** — FRAUDE
Nombre: Umbral antifraude
Descripcion: Transacciones con score < 15 requieren revision manual obligatoria...
...

Evalua cada politica y devuelve el array JSON.
```

### Example Expected Output

```json
[
  {
    "policy_code": "POL-EXC-003",
    "verdict": "BLOCKER",
    "reasoning": "Transaccion TXN-00051 usa metodo de pago Cripto. POL-EXC-003 establece que las transacciones con criptomonedas son irreversibles. BLOCKER obligatorio sin excepcion.",
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

### Changelog

- **v1.0** (2025-01): Initial release. 5-verdict system. Hard-coded Cripto=BLOCKER and FRD-001 threshold rules.

---

## Prompt 2: v1_resolution

**File:** `api/app/llm/prompts/v1_resolution.py`

### Purpose

Synthesize all available evidence — policy verdicts, historical precedents, event logs, merchant risk profile, and client history — into a final chargeback resolution recommendation. This is the second LLM call in the `/api/analyze/resolve` pipeline.

### Role

Analista senior de contracargos (senior chargeback analyst) at a Latin American fintech.

### Input Specification

| Parameter | Type | Description |
|---|---|---|
| `transaction` | `dict` | Full transaction record |
| `policy_verdicts` | `str` | JSON string of `PolicyVerdict[]` from v1_policy_eval |
| `similar_cases` | `str` | Formatted precedents from Qdrant `historical_cases` |
| `log_summary` | `str` | Summary of anomalies from transaction event logs |
| `merchant_risk` | `dict` | Merchant risk profile (chargeback rate, dispute history) |
| `client_history` | `dict` | Client chargeback history (count, average amount, outcome) |
| `motivo` | `str \| None` | Stated reason for the chargeback |
| `cliente_vip` | `bool` | Whether the client holds VIP status |
| `precedent_count` | `int` | Number of precedents found |
| `log_count` | `int` | Total number of log events |

### Output Specification

```json
{
  "transaction_id": "TXN-XXXXX",
  "recommended_action": "APPROVE | REJECT | ESCALATE | PENDING_HITL",
  "confidence": 0.0,
  "justification": "Explanatory text citing specific evidence",
  "policy_verdicts": [{"policy_code": "...", "verdict": "...", "reasoning": "...", "requires_human_review": false}],
  "precedent_summary": "Summary of similar historical cases found",
  "log_summary": "Summary of detected log anomalies",
  "risk_level": "BLOCKER | HIGH | MEDIUM | LOW",
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "next_steps": ["Step 1", "Step 2"],
  "requires_hitl": false,
  "hitl_reason": null
}
```

**`recommended_action` values:**

| Value | Meaning |
|---|---|
| `APPROVE` | Chargeback is valid — approve and refund |
| `REJECT` | Chargeback is not valid — deny refund |
| `ESCALATE` | Route to specialized team (legal, fraud unit) |
| `PENDING_HITL` | Insufficient evidence for automated decision — analyst must review |

**`risk_level` determination (embedded in prompt):**

| Level | Condition |
|---|---|
| `BLOCKER` | At least one BLOCKER verdict in policy_verdicts |
| `HIGH` | Multiple FAIL verdicts or fraud_score < 15 |
| `MEDIUM` | One FAIL verdict or fraud_score between 15 and 30 |
| `LOW` | Only PASS/WARNING/NOT_APPLICABLE and fraud_score >= 30 |

### Strict Rules Embedded in Prompt

1. If at least one BLOCKER verdict → `recommended_action` MUST be `"REJECT"` — no exceptions
2. If FAIL verdicts (no BLOCKER) and risk_level is HIGH → `recommended_action` is `"PENDING_HITL"`
3. Always cite policy codes (POL-FRD-001, POL-EXC-003, etc.) in the justification
4. NEVER invent data — if information is missing, state "No disponible"
5. `compensation_applicable` is true ONLY if SLA was breached (POL-SLA-004)
6. `compensation_amount_usd` maximum is USD 15 per POL-SLA-004
7. `next_steps` must contain 2–5 concrete, actionable steps in logical order
8. `confidence` must genuinely reflect certainty (0.0 = very uncertain, 1.0 = completely certain)
9. Respond ONLY with valid JSON in Spanish

### Analytical Precedent Usage (v1.1)

The prompt includes explicit instructions for deep precedent analysis:

| Instruction | Purpose |
|---|---|
| **Identify operational patterns** | Determine if similar cases (by merchant/method/motive) were resolved for/against the client, and why |
| **Extract concrete learnings** | Articulate what each precedent implies for the current case, citing case IDs and outcomes |
| **Contrast differences** | When a precedent has an opposite outcome, explain why the current case differs |
| **Impact on confidence** | If no precedents exist, state how this affects recommendation certainty |

### Contradiction Resolution (v1.1)

When contradictory signals exist (e.g., high fraud_score = low fraud probability vs. motive = "doesn't recognize purchase" = possible fraud), the prompt requires:

1. Explicitly identify the contradiction
2. Explain what each signal means (e.g., "fraud_score=84 means the anti-fraud system considers this LOW risk")
3. Propose what additional evidence would resolve the ambiguity

### Provisional Determinations (v1.1)

When a determination depends on subsequent verification (e.g., compensation pending SLA date audit), the prompt requires marking it as provisional in both the justification AND next_steps, preventing HITL analysts from assuming finality.

### Example Expected Output

```json
{
  "transaction_id": "TXN-00051",
  "recommended_action": "REJECT",
  "confidence": 0.97,
  "justification": "TXN-00051 involucra una transaccion con Cripto (POL-EXC-003 BLOCKER) y score antifraude de 8/100 (POL-FRD-001 BLOCKER). Las criptomonedas son irreversibles por definicion. Precedente CASO-00023 con perfil similar (Cripto, score=12) fue rechazado.",
  "policy_verdicts": [...],
  "precedent_summary": "CASO-00023: contracargo cripto rechazado. CASO-00041: score < 15 rechazado con retencion de fondos.",
  "log_summary": "3 eventos ERROR: FRAUD_ALERT, AUTH_DECLINED x2. Patron consistente con fraude confirmado.",
  "risk_level": "BLOCKER",
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "next_steps": [
    "Notificar al cliente el rechazo citando POL-EXC-003 y POL-FRD-001",
    "Reportar transaccion al equipo de fraude para investigacion",
    "Bloquear comercio CryptoVault SA si tasa de contracargos supera el 2%",
    "Archivar caso con clasificacion FRAUDE_CONFIRMADO"
  ],
  "requires_hitl": false,
  "hitl_reason": null
}
```

### Changelog

- **v1.0** (2025-01): Initial release. Includes 8 strict rules, 4-action vocabulary, SLA compensation cap at USD 15.
- **v1.1** (2025-07): Added analytical precedent usage instructions (pattern extraction, learning articulation, difference contrasting). Added contradiction resolution protocol. Added provisional determination flagging. Improved example to demonstrate HITL case with precedent analysis.

---

## Prompt 3: v1_judge

**File:** `api/app/llm/prompts/v1_judge.py`

**Execution path:** Called directly from n8n `[Juez de Calidad]` HTTP Request node → `POST https://api.anthropic.com/v1/messages` using `$env.CB_ANTHROPIC_API_KEY`. Response is parsed by `[Extraer Evaluación — Juez]` Set node via `JSON.parse($json.content[0].text)`. The FastAPI `/api/analyze/judge` route still exists for standalone testing but is bypassed in the main workflow.

### Purpose

Act as an independent quality supervisor evaluating the resolution produced by v1_resolution. This implements the LLM-as-Judge pattern. The Judge score gates both human escalation and auto-indexing of new precedents.

### Role

Supervisor de calidad de resoluciones (resolution quality supervisor) at a Latin American fintech.

### Input Specification

| Parameter | Type | Description |
|---|---|---|
| `full_context` | `dict` | Complete evidence package: transaction + policies + precedents + logs + merchant + client |
| `resolution` | `dict` | Resolution JSON produced by v1_resolution |

### Output Specification

```json
{
  "overall_score": 7.5,
  "criteria": {
    "policy_consistency": 8.0,
    "justification_quality": 7.0,
    "precedent_usage": 7.5,
    "risk_assessment": 8.0,
    "actionability": 7.0
  },
  "approved": true,
  "strengths": ["Concrete strength 1", "Concrete strength 2"],
  "weaknesses": ["Concrete weakness 1", "Concrete weakness 2"]
}
```

### Scoring Criteria (each 1.0–10.0)

| Criterion | Key | Scoring guidance |
|---|---|---|
| Policy consistency | `policy_consistency` | Resolution respects all BLOCKERs and FAILs. APPROVE with any active BLOCKER = **automatic 1.0** |
| Justification quality | `justification_quality` | Cites specific IDs, amounts, scores, policy codes = high score; vague justification = low score |
| Precedent usage | `precedent_usage` | Mentions specific cases and extracts learnings = high; ignores precedents = low |
| Risk assessment | `risk_assessment` | Assigned risk_level is correct given the verdicts and fraud_score |
| Actionability | `actionability` | next_steps are concrete, achievable, and relevant to this specific case |

`overall_score` = arithmetic average of all 5 criteria.
`approved` = `true` if `overall_score >= 7.0`.

### Gate Logic (used by the system, not the prompt)

| Score range | Outcome |
|---|---|
| >= 8.0 | Approved + auto-indexed as new precedent in Qdrant |
| 7.0–7.9 | Approved — delivered to analyst as final resolution |
| 5.0–6.9 | Not approved — routed to HITL for analyst review |
| < 5.0 | Not approved + flagged `needs_review=true` in feedback record |

### Changelog

- **v1.0** (2025-01): Initial release. 5-criterion scoring. APPROVE+BLOCKER = automatic 1.0 on `policy_consistency`.

---

## Log Analysis (deterministic — no LLM prompt)

**Implementation:** `api/app/analysis/analyzer.py` → `Analyzer.count_severities()` + `Analyzer.detect_error_patterns()`
**Pipeline integration:** `ResolutionService._summarize_logs()` in `api/app/services/resolution.py`

### Purpose

Analyze payment processing event logs to count severities and detect anomaly patterns. Unlike the other three prompts, log analysis is **deterministic** — it does not use an LLM call. The structured nature of log events (severity, event name, service) makes rule-based pattern matching more reliable, faster, and cheaper than LLM analysis.

### How it works

1. `Analyzer.count_severities(logs)` produces `{"ERROR": N, "WARN": N, "INFO": N}`
2. `Analyzer.detect_error_patterns(logs)` scans for 9 known anomaly patterns by event name
3. `ResolutionService._summarize_logs()` combines both outputs into a text summary that is passed to the `v1_resolution` prompt as the `log_summary` parameter

The LLM (v1_resolution) receives the pre-computed summary and interprets it alongside other evidence — it never processes raw logs directly.

### 9 Anomaly Patterns Detected (deterministic)

| Pattern | Description | Related policy |
|---|---|---|
| `MERCHANT_NO_RESPONSE` x2+ | Systematic merchant timeout | POL-CB-002 |
| `TIMEOUT_RETRY` | Connectivity or system overload | — |
| `FRAUD_ALERT + AUTH_DECLINED` | Transaction blocked for fraud | POL-FRD-001 |
| `SESSION_EXPIRED` during `PAYMENT_INITIATED` | Payment interrupted by expired session | — |
| `WEBHOOK_FAILED` | Integration failure with merchant system | — |
| `DOUBLE_CHARGE_DETECT` | Possible duplicate charge | — |
| `SLA_BREACH` | SLA violation detected by system | POL-SLA-002 |
| `GEO_ANOMALY` | Geographic anomaly | POL-FRD-002 |
| `AUTH_DECLINED` multiple | Repeated failed authorization attempts | POL-FRD-001 |
| `ERROR` sequence | Systemic processing failure | — |

### Design rationale

Log events have structured fields (`severity`, `event`, `service`, `code`) that map directly to known anomaly types. Using pattern matching instead of an LLM call eliminates one API round-trip per investigation (~300ms + token cost) with zero accuracy loss.

---

## Prompt Engineering Principles

### Why separate Python modules, not inline strings

Every prompt lives in a dedicated file with a version comment at line 1. This enables:
- Git diff shows exactly what changed between versions
- `render()` functions can be unit-tested independently
- Multiple versions can coexist (`v1_policy_eval.py`, `v2_policy_eval.py`) during rollout
- IDE tooling (linting, search) works normally on prompt text

### Why Spanish-language prompts

The dataset, policies, and log messages are in Spanish. Using Spanish prompts eliminates translation overhead and reduces the risk of semantic drift when the LLM translates concepts internally. The embedding model (`voyage-multilingual-2` via Voyage AI) is explicitly multilingual and handles Spanish natively.

### Temperature setting

All four prompts run at `temperature=0.3` (configurable via `CB_LLM_TEMPERATURE`). This provides a balance between deterministic policy compliance (closer to 0.0) and natural language justification quality (closer to 0.5). Pure factual tasks (policy_eval, judge) benefit most from low temperature.

### Structured output enforcement

All prompts instruct the LLM to "Responde UNICAMENTE con JSON valido. Sin texto adicional." The `parse_json_safely()` function in `llm/parsing.py` provides a fallback parser that strips markdown code fences and finds embedded JSON if the model adds surrounding text despite the instruction. For v1_judge (called from n8n), the `[Extraer Evaluación — Juez]` Set node uses a robust expression: `JSON.parse($json.content[0].text.replace(/```json\n?/g, '').replace(/```\n?/g, '').trim())`.
