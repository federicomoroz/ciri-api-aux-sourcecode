# Mejora Continua — CIRI Chargeback Agent

This document describes the auto-improvement system: how analyst decisions feed back into the agent's knowledge base, how the Judge quality gate controls what gets learned, how hallucinations are detected and corrected, and how the system is observed in production.

---

## Feedback Loop — End to End

The feedback loop closes the gap between automated resolution and human expertise. Each analyst decision is a training signal that makes future resolutions better.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        FULL FEEDBACK LOOP                                   │
│                                                                             │
│  1. CASE ARRIVES                                                            │
│     Webhook → n8n AI Agent → FastAPI pipeline                              │
│                                                                             │
│  2. AGENT RESOLVES                                                          │
│     RAG (policies + precedents) → LLM → Resolution JSON                   │
│                                                                             │
│  3. JUDGE EVALUATES                                                         │
│     POST /api/analyze/judge → overall_score (1.0–10.0)                    │
│           │                                                                 │
│           ├── score >= 7.0 ──────────────────────────────────┐             │
│           │                                                   │             │
│           └── score < 7.0 → HITL ──────────────────────────►│             │
│                              Analyst reads HTML report        │             │
│                              Analyst overrides or confirms    │             │
│                                                               ▼             │
│  4. FEEDBACK SUBMITTED                                                      │
│     POST /api/feedback/ {transaction_id, analyst_decision,                 │
│                          final_outcome, judge_score, resolution}            │
│                                │                                            │
│                                ▼                                            │
│  5. SQLITE RECORD                                                           │
│     feedback table: decision, notes, judge_score, timestamp                │
│                                │                                            │
│               judge_score >= 8.0?                                           │
│                    │                                                         │
│          YES ──────┴────── NO                                               │
│           │                 └──► Saved to SQLite only (audit trail)        │
│           ▼                                                                 │
│  6. QDRANT AUTO-INDEX                                                       │
│     QdrantIndexer.index_single_case(case, tx)                              │
│     New precedent in historical_cases collection                            │
│                                │                                            │
│           ┌────────────────────┘                                            │
│           ▼                                                                 │
│  7. NEXT SIMILAR CASE                                                       │
│     GET /api/cases/similar → finds new precedent (similarity >= 0.40)     │
│     v1_resolution receives it as context → better-informed decision        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What the analyst sees

The analyst receives a Jinja2-rendered HTML report (`GET /api/reports/{id}`) containing:
- Transaction details (amount, merchant, country, payment method, fraud_score)
- Policy verdicts with reasoning for each violated policy
- Similar historical cases with resolution outcomes
- The agent's recommended action and justification
- Judge scores per criterion with strengths and weaknesses
- Next steps proposed by the agent

The analyst can confirm the agent's recommendation or override it (APPROVE/REJECT/ESCALATE) and add free-text notes explaining the override.

### Feedback payload

```bash
POST /api/feedback/
{
  "transaction_id": "TXN-00042",
  "analyst_decision": "APPROVED",
  "analyst_notes": "Cliente VIP con historial limpio de 18 meses. Riesgo aceptado.",
  "final_outcome": "APPROVED",
  "judge_score": 8.2,
  "resolution": { /* full Resolution JSON from the agent */ }
}
```

### Feedback response

```json
{
  "status": "recorded",
  "feedback_id": 15,
  "auto_indexed": true,
  "needs_review": false,
  "judge_score": 8.2
}
```

`auto_indexed: true` confirms the case was added as a Qdrant precedent.

---

## Judge Gate Mechanism

The LLM-as-Judge (`v1_judge` prompt) provides an independent quality evaluation of every resolution. It evaluates 5 criteria, each scored 1.0–10.0, producing an `overall_score`.

### 5 Criteria

| Criterion | Description | Critical failure condition |
|---|---|---|
| `policy_consistency` | Resolution respects all BLOCKER and FAIL verdicts | APPROVE with active BLOCKER → automatic score 1.0 |
| `justification_quality` | Justification cites specific evidence (IDs, amounts, scores, policy codes) | Vague or generic justification |
| `precedent_usage` | Resolution leverages similar historical cases | Ignores precedents entirely |
| `risk_assessment` | Assigned `risk_level` matches the evidence | Inconsistent risk level vs. verdicts |
| `actionability` | `next_steps` are concrete, achievable, and case-specific | Vague or inapplicable steps |

### Score thresholds and outcomes

```
Score range    Outcome
───────────────────────────────────────────────────────────────────
>= 8.0         Approved + auto-indexed as new Qdrant precedent
7.0 – 7.9      Approved — delivered as final resolution
5.0 – 6.9      Not approved — routed to HITL (analyst review required)
< 5.0          Not approved + flagged needs_review=true in SQLite
```

### Why 7.0 as the HITL threshold

A score of 7.0 means all 5 criteria averaged at least 7/10. Resolutions below this threshold typically have at least one of: incomplete policy reasoning, unused precedents, or vague next steps. These are exactly the cases where analyst review adds the most value. Setting the threshold lower (e.g., 6.0) would push too many mediocre resolutions through without review.

### Why 8.0 as the auto-index threshold

Auto-indexed cases become permanent precedents that influence future resolutions. Only high-quality cases (8.0+ = "good" across all 5 criteria) should become training data. A case indexed with a 6.5 score might have weak justification or poor precedent usage — learning from it could degrade future resolutions.

### Judge as hallucination detector

The Judge's `policy_consistency` criterion detects the most dangerous hallucination: recommending `APPROVE` when a `BLOCKER` verdict was produced. If the Judge sees this, it assigns `policy_consistency = 1.0` and the `overall_score` drops below 7.0, routing to HITL. The guardrail in `/api/analyze/resolve` catches this before the Judge even runs (see Guardrails section).

---

## Hallucination Detection — Guardrails

Three post-LLM guardrails run in `_validate_resolution()` inside `POST /api/analyze/resolve`, applied to the Resolution JSON before it is returned to n8n.

### Guardrail 1: APPROVE with active BLOCKER

**Condition:** `recommended_action == "APPROVE"` AND at least one policy verdict has `verdict == "BLOCKER"`

**Action:** Auto-correct — force `recommended_action = "REJECT"` and `risk_level = "BLOCKER"`

**Warning appended:**
```
"GUARDRAIL: APPROVE con BLOCKER activo — auto-corregido a REJECT (posible alucinacion)"
```

**Why it matters:** This is the most dangerous possible hallucination. Approving a chargeback for a cryptocurrency transaction (irreversible by design) or a confirmed fraud case would result in financial loss. The guardrail catches this even when the LLM produces contradictory output.

**Implementation:**
```python
has_blocker = any(v.get("verdict") == "BLOCKER" for v in resolution.get("policy_verdicts", []))
if resolution.get("recommended_action") == "APPROVE" and has_blocker:
    resolution["recommended_action"] = "REJECT"
    resolution["risk_level"] = "BLOCKER"
```

### Guardrail 2: Compensation exceeds transaction amount

**Condition:** `compensation_amount_usd > transaction.amount_usd * 1.10`

**Action:** Append warning (no auto-correction — human must decide)

**Warning appended:**
```
"GUARDRAIL: Compensacion USD 25.00 excede el monto original USD 18.50 en >10%"
```

**Why it matters:** POL-SLA-004 caps compensation at USD 15. An LLM might hallucinate a compensation amount based on the transaction value rather than the fixed cap. This guardrail flags the anomaly without forcing a correction (the analyst may have legitimate override reasons).

### Guardrail 3: Excessive confidence with multiple policy failures

**Condition:** `confidence > 0.95` AND `fail_count >= 2` (FAIL or BLOCKER verdicts)

**Action:** Append warning (no auto-correction)

**Warning appended:**
```
"GUARDRAIL: Confianza excesiva (0.97) con 3 violaciones de politica"
```

**Why it matters:** High confidence (>0.95) is appropriate only when evidence is unambiguous. Multiple policy failures indicate a complex case where certainty should be lower. A resolution claiming 97% confidence with 3 FAIL verdicts is likely overconfident and warrants analyst review.

### Guardrail summary

| Guardrail | Auto-correct | HITL trigger | Severity |
|---|---|---|---|
| APPROVE + BLOCKER | Yes (force REJECT) | No (resolved automatically) | Critical |
| Compensation > 110% | No | Yes (warning logged) | High |
| Confidence > 95% with 2+ FAILs | No | Yes (warning logged) | Medium |

---

## RAG Auto-Update — Triggers and Behavior

### Trigger: `on_policy_created` (POST /api/policies/)

```
REST call → db.upsert_policy() → RAGUpdater.on_policy_created(policy)
                                          │
                                          └──► QdrantIndexer.index_single_policy(policy)
                                               [new Markdown embedding in 'policies' collection]
                                               [immediately available to next resolve request]
```

**Effect:** New policy is immediately retrievable by semantic search. No restart required.

### Trigger: `on_policy_updated` (PUT /api/policies/{code})

```
REST call → db.upsert_policy() → RAGUpdater.on_policy_updated(policy)
                                          │
                                          ├──► QdrantIndexer.delete_policy(code)
                                          │    [removes old point by deterministic UUID]
                                          └──► QdrantIndexer.index_single_policy(policy)
                                               [inserts new point with updated embedding]
```

**Effect:** The old embedding (based on old description) is removed and replaced with the new one. If the policy description changed (e.g., threshold updated from 15 to 20), the new semantic meaning is reflected immediately.

### Trigger: `on_policy_deleted` (DELETE /api/policies/{code})

```
REST call → db.delete_policy(code) → RAGUpdater.on_policy_deleted(code)
                                              │
                                              └──► QdrantIndexer.delete_policy(code)
                                                   [point removed by deterministic UUID]
```

**Effect:** Deleted policy is no longer retrievable. The next resolve request will not include it in the policy evaluation context.

### Trigger: `on_case_resolved` (POST /api/feedback/, judge_score >= 8.0)

```
POST /api/feedback/ → db.save_feedback() → RAGUpdater.on_case_resolved(case, 8.5)
                                                    │
                                               8.5 >= 8.0?  YES
                                                    │
                                               db.get_transaction(transaction_id)
                                                    │
                                               QdrantIndexer.index_single_case(case, tx)
                                               [new point in 'historical_cases']
                                               [returns True → auto_indexed=true in response]
```

**Effect:** High-quality resolved case becomes a permanent precedent. All future similar cases will retrieve it as a top-5 example.

---

## Prompt Versioning Strategy

### File naming convention

```
api/app/llm/prompts/
  v1_policy_eval.py       ← current production version
  v1_resolution.py
  v1_judge.py
  v1_log_analysis.py
```

Each file begins with a version header comment:
```python
# PROMPT VERSION: v1.0 | DATE: 2025-01 | CHANGES: initial release
```

### Version promotion workflow

When a prompt needs updating:

1. **Create new version file:** Copy `v1_resolution.py` → `v2_resolution.py`
2. **Edit the new file:** Make the prompt changes, update the header comment
3. **Update the import in `__init__.py`:** `from . import v2_resolution as resolution`
4. **Run regression tests:** `pytest tests/unit/test_resolution_prompt.py`
5. **Shadow mode (optional):** Call both v1 and v2 in parallel, compare outputs, log to Langfuse
6. **Promote:** Update `__init__.py` import, remove shadow call
7. **Keep v1 file:** Do not delete — needed for rollback and audit

### Why versioned files, not inline strings

- **Git blame** shows exactly when each prompt line changed and why
- **Rollback** is a one-line import change, not a multi-line string edit
- **A/B testing** is straightforward: call both versions, compare Judge scores
- **Unit tests** import the render function directly: `from app.llm.prompts.v1_policy_eval import render`
- **IDE tooling** (linting, search, refactor) works on Python files, not embedded string literals

---

## Observability — Langfuse Metrics

Langfuse is configured via `CB_LANGFUSE_ENABLED=true` and credentials in `.env`. All LLM calls are traced via `LangfuseTracer` (injected as `tracer` dependency in routes).

### Metrics tracked

| Metric | Langfuse field | Tracked at |
|---|---|---|
| Token count (input/output) | `usage.input_tokens`, `usage.output_tokens` | Every LLM call |
| Latency per call | Span duration | Every LLM call |
| Token cost (estimated) | `usage.total_cost` | Every LLM call |
| Judge overall score | Custom score `judge_score` | POST /api/analyze/judge |
| Analyst feedback score | Custom score `analyst_feedback_judge_score` | POST /api/feedback/ |
| Cache hit | Span attribute `cache_hit: true/false` | Before LLM calls |
| Error rate | Failed spans | Every LLM call |

### Dashboard use cases

**Cost tracking:**
Langfuse aggregates token usage by model, route, and time period. Haiku at ~$0.001/1K tokens makes this affordable, but the dashboard reveals if any prompt is consuming disproportionate tokens (e.g., a very long log summary being passed to v1_resolution).

**Judge score trends:**
Tracking `judge_score` over time shows whether resolution quality is improving as new precedents are indexed. A declining trend signals that recently auto-indexed cases may be low-quality.

**Cache hit rate:**
High cache hit rate (> 20%) indicates the system is handling many similar cases efficiently. Low rate may indicate the corpus is too diverse or the threshold (0.92) is too strict.

**Latency breakdown:**
Langfuse traces each prompt call separately (policy_eval, resolution, judge). If latency spikes, the trace identifies which prompt call is the bottleneck.

**Error rate:**
Failed JSON parse (despite `_parse_json_safely`), Qdrant connection errors, or Anthropic API rate limits are captured as failed spans with error messages.

### Langfuse trace structure per resolution

```
Trace: POST /api/analyze/resolve [TXN-00051]
  ├── Span: qdrant_cache_check [hit=false, latency=12ms]
  ├── Span: v1_policy_eval [tokens=1847, latency=890ms]
  ├── Span: v1_resolution [tokens=3204, latency=1340ms]
  ├── Span: guardrails [warnings=1: APPROVE+BLOCKER corrected]
  └── Score: judge_score = 0.0 (not yet judged — separate call)

Trace: POST /api/analyze/judge [TXN-00051]
  ├── Span: v1_judge [tokens=2156, latency=1120ms]
  └── Score: judge_score = 9.2
```

### Enabling observability

```env
# .env
CB_LANGFUSE_ENABLED=true
CB_LANGFUSE_PUBLIC_KEY=pk-lf-...
CB_LANGFUSE_SECRET_KEY=sk-lf-...
CB_LANGFUSE_HOST=https://cloud.langfuse.com
```

When `CB_LANGFUSE_ENABLED=false` (default), all tracer calls are no-ops — the `LangfuseTracer` wrapper returns empty objects that absorb method calls without error.
