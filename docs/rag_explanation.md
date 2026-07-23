# RAG Strategy — CIRI Chargeback Agent

This document explains what is indexed in Qdrant, what is not, why each choice was made, how retrieval works, and how the vector store stays current as the system operates.

---

## What Gets Indexed (and Why)

### 1. Policies — `policies` collection

**Why:** Policies are natural language documents. Their applicability to a transaction cannot be determined by exact string matching; it requires semantic understanding. "This payment method is irreversible" is semantically relevant to a Cripto transaction even if neither the transaction record nor the search query contains the exact word "irreversible".

**What:** 17 policy documents (4 FRAUDE, 5 CHARGEBACK, 4 SLA, 4 EXCEPCION) converted to Markdown format before embedding:

```markdown
# POL-EXC-003
**Categoria:** EXCEPCION
**Politica:** Exclusion de criptomonedas
## Descripcion
Las transacciones realizadas con criptomonedas son irreversibles por naturaleza...
**Referencia:** Reg. Fintech 2024/03
```

The Markdown format preserves hierarchy and emphasis, which the embedding model encodes into the vector representation.

**Point ID strategy:** Deterministic `uuid5(NAMESPACE_DNS, policy_code)` — the same code always produces the same point ID. This makes `upsert` idempotent: re-indexing a policy after editing does not create duplicates.

### 2. Historical Cases — `historical_cases` collection

**Why:** Past resolved cases provide precedents. When a new chargeback arrives for a Visa credit card in Argentina with a mid-range fraud score, finding 5 cases with similar merchant type, payment method, and fraud profile helps the LLM reason about likely outcomes without hallucinating.

**What:** 60 historical cases, each encoded as a searchable text combining case attributes and transaction context:

```
Caso CASO-00042: contracargo por cargo no reconocido
Resolucion: APPROVED
en TechStore AR, Credito Visa, ARG,
USD 234.50, score antifraude 4
Observaciones: Cliente VIP, primer contracargo en 18 meses
```

Transaction context (merchant, amount, payment method, country, fraud_score) is embedded alongside case outcome. This lets the retriever find cases similar not just in description but in financial profile.

**Auto-growth:** New cases are indexed on every `POST /api/feedback/` call where `judge_score >= 8.0`. The collection starts at 60 and grows over time.

### 3. Semantic Cache — `_semantic_cache` collection

**Why:** Two transactions with nearly identical profiles (same merchant, similar amount, same payment method, same country, similar fraud score) will produce nearly identical resolution requests. Running the full LLM pipeline twice wastes tokens and adds latency.

**What:** Cached resolution responses, indexed by the resolution request query string. Threshold for cache hit: cosine similarity >= 0.92 (near-identical queries only — not just semantically related ones).

**Format:**
```json
{
  "query": "Contracargo en TechStore AR por USD 234.50, Credito Visa, ARG, score 4",
  "response": { /* full Resolution JSON */ }
}
```

---

## What Does NOT Get Indexed (and Why)

### Transactions

**Not in Qdrant.** Transactions are retrieved by exact ID (`GET /api/transactions/{id}`) or filtered by structured fields (amount range, date range, payment method). These are structured queries with no semantic ambiguity. SQLite handles this efficiently with indexed columns.

Indexing 100 transactions in Qdrant would be wasteful: "TXN-00051" is not semantically similar to anything — you look it up by its exact ID.

### Event Logs

**Not in Qdrant.** Logs are structured records with `{timestamp, severity, event, service, code, detail}`. They are retrieved via `GET /api/logs/{tx_id}` (exact match on transaction ID). Log analysis is **deterministic** — `Analyzer.detect_error_patterns()` extracts named patterns (SYSTEMATIC_MERCHANT_TIMEOUT, CONNECTIVITY_ISSUE, etc.) and `Analyzer.count_severities()` produces severity counts. A text summary of critical logs is passed to the `v1_resolution` prompt as context. There is no need to pre-index logs for similarity search.

Indexing logs in Qdrant would complicate the pipeline without benefit: you always retrieve logs for a specific transaction, never ask "find me logs similar to this log entry".

### Feedback Records

**Not in Qdrant.** Analyst feedback is structured data (transaction_id, decision, notes, judge_score, timestamp). It is stored in SQLite and used for analytics and audit. The _outcome_ of high-quality feedback (judge_score >= 8.0) results in a new case being indexed in `historical_cases`, but the raw feedback record itself is not indexed.

---

## Qdrant Collections — Detailed Specification

### Collection: `policies`

| Parameter | Value |
|---|---|
| Vector size | 1024 |
| Distance metric | Cosine |
| Total points | 17 (static, grows only via POST /api/policies/) |
| Retrieval `top_k` | 17 |
| Retrieval `score_threshold` | 0.0 |
| Purpose | Return ALL policies; LLM filters relevance |

**Payload schema per point:**

```json
{
  "code": "POL-EXC-003",
  "name": "Exclusion de criptomonedas",
  "category": "EXCEPCION",
  "description": "Las transacciones con criptomonedas...",
  "reference": "Reg. Fintech 2024/03",
  "markdown": "# POL-EXC-003\n**Categoria:** ..."
}
```

**Why threshold=0.0:** The corpus is small (17 documents). Filtering by similarity score risks excluding a highly relevant policy that happens to use different vocabulary. The LLM is more reliable than a cosine threshold for determining policy applicability — it can read the policy description and decide "NOT_APPLICABLE" with reasoning.

### Collection: `historical_cases`

| Parameter | Value |
|---|---|
| Vector size | 1024 |
| Distance metric | Cosine |
| Starting points | 60 |
| Retrieval `top_k` | 5 |
| Retrieval `score_threshold` | 0.40 |
| Purpose | Precedent retrieval for resolution context |

**Payload schema per point:**

```json
{
  "case_id": "CASO-00042",
  "transaction_id": "TXN-00042",
  "motivo": "Cargo no reconocido",
  "resolution": "APPROVED",
  "resolution_days": 7,
  "analyst": "ana.garcia",
  "observations": "Cliente VIP primer contracargo",
  "merchant": "TechStore AR",
  "amount_usd": 234.50,
  "payment_method": "Credito Visa",
  "country": "ARG",
  "fraud_score": 4,
  "_text": "Caso CASO-00042: contracargo por cargo no reconocido..."
}
```

**Why threshold=0.40:** For precedents, only semantically meaningful matches should be included. A case with 38% similarity (e.g., different country, different payment method) would be a poor precedent and could mislead the LLM. The threshold filters out weak matches while still catching cases that share key financial profile attributes.

### Collection: `_semantic_cache`

| Parameter | Value |
|---|---|
| Vector size | 1024 |
| Distance metric | Cosine |
| Total points | Grows unbounded |
| Retrieval `top_k` | 1 |
| Retrieval `score_threshold` | 0.92 |
| Purpose | Avoid redundant LLM calls for near-identical cases |

**Why threshold=0.92:** The cache must only return results when the new request is nearly identical to a cached one. A lower threshold would return cached resolutions for cases that differ in amount, fraud score, or country — differences that meaningfully change the resolution. At 0.92, only requests with the same merchant, same approximate amount, and same payment method profile will hit the cache.

---

## Embedding Model

**Model:** `voyage-multilingual-2` (Voyage AI API)

**Dimensions:** 1024

**Why this model:**

| Criterion | Rationale |
|---|---|
| Multilingual | Dataset, policies, and logs are in Spanish. The model handles Spanish natively without translation overhead. |
| High quality (1024 dims) | Superior semantic similarity for paraphrase detection. "Cargo no reconocido" and "compra desconocida" produce highly similar embeddings. |
| Paraphrase-tuned | Explicitly trained for semantic similarity, not just keyword overlap. |
| Free tier | Voyage AI offers a generous free tier — no cost for development and testing. |
| Lazy loading | Client initialized on first `encode()` call via double-checked locking. Zero RAM at startup. |
| Thread-safe | `FastEmbedder` uses `threading.Lock` for safe concurrent access. |
| Configurable | API key passed via constructor (`api_key` param) or `CB_VOYAGE_API_KEY` env var. |

**Configuration:**
```env
CB_VOYAGE_API_KEY=pa-...          # Required — get a free key at https://dash.voyageai.com/
CB_EMBEDDING_MODEL=voyage-multilingual-2
CB_EMBEDDING_DIM=1024
```

**Alternatives considered:**

- `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers): 384 dims, runs locally but lower quality and requires ONNX runtime in Docker
- `text-embedding-3-small` (OpenAI): requires paid API, vendor lock-in with LLM provider
- `all-MiniLM-L6-v2`: English-only, poor Spanish performance

---

## QueryBuilder Enrichment Strategy

The `QueryBuilder` class in `api/app/rag/retriever.py` builds the Qdrant search query without calling an LLM. It starts from a base query and appends domain-specific terms based on transaction fields.

### Policy query construction

```python
base = f"contracargo {motivo}, {channel}, {payment_method}, score {fraud_score}/100, {country}"

# Rule 1: Cripto transactions → irreversibility keywords
if payment_method == "Cripto":
    base += " criptomonedas no reversible blocker"

# Rule 2: Low fraud score → risk keywords
if fraud_score < 30:
    base += " transaccion de alto riesgo fraude score bajo"

# Rule 3: Non-LATAM country → extended SLA keywords
if country not in {"ARG", "BRA", "CHL", "COL", "MEX", "PER", "URY"}:
    base += " internacional fuera LATAM plazo extendido"

# Rule 4: IVR channel → amount limit keywords
if channel == "IVR":
    base += " limite monto IVR"
```

### Example enriched queries

| Transaction | Enriched Query |
|---|---|
| Cripto, score=8, ARG | `"contracargo Cargo no reconocido, Web, Cripto, score 8/100, ARG criptomonedas no reversible blocker transaccion de alto riesgo fraude score bajo"` |
| Visa, score=4, USA | `"contracargo Cargo no reconocido, Web, Credito Visa, score 4/100, USA transaccion de alto riesgo fraude score bajo internacional fuera LATAM plazo extendido"` |
| Debito, score=8, ARG | `"contracargo Cargo no reconocido, Web, Debito Visa, score 8/100, ARG"` |

### Why deterministic enrichment beats LLM-generated queries

1. **Reproducibility:** The same transaction always produces the same query. Test results are stable.
2. **Zero cost:** No token consumption at retrieval time.
3. **Debuggability:** The enriched query is logged in the response (`_query` field) and can be inspected directly.
4. **Vocabulary alignment:** The appended terms are copied from the actual policy descriptions, maximizing cosine similarity.

---

## Retrieval Parameters Summary

| Collection | top_k | score_threshold | Rationale |
|---|---|---|---|
| `policies` | 17 | 0.0 | Small corpus — retrieve all, LLM filters |
| `historical_cases` | 5 | 0.40 | Only meaningfully similar precedents |
| `_semantic_cache` | 1 | 0.92 | Near-identical match only |

---

## Auto-Update Loop

```
                      POST /api/policies/
                      PUT  /api/policies/{code}
                      DELETE /api/policies/{code}
                              │
                              ▼
                   RAGUpdater.on_policy_created/updated/deleted()
                              │
                              ├──► SQLite upsert / delete
                              └──► Qdrant upsert / delete (immediate)
                                   [next resolve request uses updated policy]


                      POST /api/feedback/
                       {judge_score: 8.5, resolution: {...}, ...}
                              │
                              ▼
                   RAGUpdater.on_case_resolved(case, judge_score)
                              │
                      judge_score >= 8.0?
                              │
                    YES ──────┼────── NO
                              │           └──► Save to SQLite only
                              ▼
                   QdrantIndexer.index_single_case(case, tx)
                   [new precedent available for next similar case]
```

### Policy update flow (immediate effect)

When an analyst calls `PUT /api/policies/POL-EXC-003` to update the cryptocurrency exclusion policy description:

1. FastAPI route saves updated record to SQLite (`updated_at` timestamp updated)
2. `RAGUpdater.on_policy_updated()` is called
3. Old Qdrant point is deleted (by deterministic UUID derived from `POL-EXC-003`)
4. New point is inserted with the updated Markdown text embedded
5. The next `GET /api/policies/search` call returns the updated policy content
6. No restart, no redeploy required

### Case auto-index flow

When an analyst submits feedback with `judge_score = 8.5`:

1. `POST /api/feedback/` saves to SQLite `feedback` table
2. `RAGUpdater.on_case_resolved()` checks `8.5 >= 8.0` → True
3. Fetches the full transaction record from SQLite via `db.get_transaction()`
4. `QdrantIndexer.index_single_case(case, tx)` creates embedding and upserts into `historical_cases`
5. Point ID is deterministic (`uuid5("FB-{feedback_id}")`) — safe to call multiple times
6. Future cases with similar merchant + amount + payment method + fraud profile will retrieve this case as precedent

---

## Semantic Cache Strategy

### Cache key

The cache key is the case similarity query string built by `QueryBuilder.for_similar_cases()`:
```
"Contracargo en TechStore AR por USD 234.50, Credito Visa, ARG, score 4, motivo: Cargo no reconocido"
```

This string is embedded and stored in `_semantic_cache` alongside the full resolution response.

### Cache check (before LLM calls)

```python
cached = retriever.check_semantic_cache(query, threshold=0.92)
if cached:
    return cached  # skip all LLM calls
```

At 0.92 cosine similarity, only transactions with the same merchant, very similar amount, same payment method, and same country will hit the cache. A transaction for USD 235.00 at the same merchant may or may not hit depending on how the embedding encodes the amount difference.

### Cache write (after successful resolution)

After a full resolution pipeline completes (policy_eval + resolution + judge all pass), the response is stored in the cache:
```python
retriever.store_in_cache(query, resolution_response)
```

### Cache invalidation

There is no explicit TTL or invalidation mechanism in v1. The cache is valid as long as the policy set does not change dramatically. When a major policy update occurs, the operator can clear the `_semantic_cache` collection via the Qdrant API:
```bash
curl -X DELETE http://localhost:6333/collections/_semantic_cache
```
The collection is recreated automatically on the next API startup.
