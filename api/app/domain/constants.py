"""
Business constants — single source of truth for every threshold and limit.

All magic numbers that were previously scattered across 8+ files live here.
To change a business rule, change one line in this file.
"""

from .enums import LATAM_COUNTRIES  # re-export: canonical source is enums.py

__all__ = [
    "LATAM_COUNTRIES",
    # SLA
    "SLA_VIP_DAYS",
    "SLA_STANDARD_DAYS",
    "SLA_EXTENDED_DAYS",
    # Client risk
    "CLIENT_RECIDIVIST_THRESHOLD",
    "CLIENT_GEO_ANOMALY_THRESHOLD",
    # Merchant risk
    "MERCHANT_SUSPENDED_CB_RATIO",
    "MERCHANT_HIGH_CB_RATIO",
    "MERCHANT_STRATEGIC_VOLUME",
    # Guardrails
    "GUARDRAIL_MAX_COMPENSATION_RATIO",
    "GUARDRAIL_MAX_CONFIDENCE",
    "GUARDRAIL_MIN_FAILS_FOR_WARNING",
    # RAG
    "SIMILAR_CASES_SCORE_THRESHOLD",
    "SIMILAR_CASES_TOP_K",
    "POLICIES_TOP_K",
    "POLICIES_SCORE_THRESHOLD",
    "SEMANTIC_CACHE_THRESHOLD",
    "EMBEDDING_DIM",
    # LLM
    "LLM_TRUNCATION_LENGTH",
    "LLM_DEFAULT_MAX_TOKENS",
    "LLM_DEFAULT_MAX_RETRIES",
    "LLM_DEFAULT_TEMPERATURE",
    # Judge
    "JUDGE_APPROVAL_THRESHOLD",
    "JUDGE_NEEDS_REVIEW_THRESHOLD",
    "JUDGE_AUTO_INDEX_THRESHOLD",
    # RAG / Retrieval
    "FRAUD_SCORE_DEFAULT",
    "FRAUD_SCORE_HIGH_RISK_THRESHOLD",
    # Pattern detection
    "MERCHANT_TIMEOUT_PATTERN_MIN_COUNT",
    # LLM context limits
    "LLM_MAX_CRITICAL_LOGS",
    # Feedback / auto-index
    "FEEDBACK_MOTIVO_MAX_CHARS",
    "FEEDBACK_AUTO_RESOLUTION_DAYS",
    "FEEDBACK_AUTO_ANALYST_TAG",
    # n8n integration
    "N8N_WEBHOOK_PATH",
    "N8N_HEALTHZ_PATH",
    "N8N_TIMEOUT_S",
    "N8N_PING_TIMEOUT_S",
    # Trace / observability names
    "TRACE_RESOLVE",
    "TRACE_JUDGE",
    "TRACE_LLM_CALL",
    "TRACE_FEEDBACK",
    "TRACE_FEEDBACK_SCORE",
    # Feedback response
    "FEEDBACK_STATUS_RECORDED",
    "FEEDBACK_CASE_ID_PREFIX",
    # Fallback values
    "FALLBACK_TX_ID",
    # SLA types
    "SLA_TYPE_VIP",
    "SLA_TYPE_EXTENDED",
    "SLA_TYPE_STANDARD",
    # Health status
    "HEALTH_OK",
    "HEALTH_HEALTHY",
    "HEALTH_DEGRADED",
    # Conversion
    "SECONDS_TO_MS",
]

# ── SLA Limits ──────────────────────────────────────────────────────────────
# Sources: POL-EXC-002 (VIP), POL-SLA-002 (standard LATAM), POL-EXC-004 (non-LATAM)
SLA_VIP_DAYS: int = 5
SLA_STANDARD_DAYS: int = 10
SLA_EXTENDED_DAYS: int = 15

# ── Client Risk Thresholds ──────────────────────────────────────────────────
CLIENT_RECIDIVIST_THRESHOLD: int = 3       # chargebacks > N → "recidivist"
CLIENT_GEO_ANOMALY_THRESHOLD: int = 3      # distinct countries > N → "geo_anomaly"

# ── Merchant Risk Thresholds ────────────────────────────────────────────────
MERCHANT_SUSPENDED_CB_RATIO: float = 0.02   # cb_ratio > N → "suspended_merchant"
MERCHANT_HIGH_CB_RATIO: float = 0.01        # cb_ratio > N → "high_cb_ratio"
MERCHANT_STRATEGIC_VOLUME: float = 1_000_000.0  # total_volume_usd > N → is_strategic

# ── Guardrails ──────────────────────────────────────────────────────────────
GUARDRAIL_MAX_COMPENSATION_RATIO: float = 1.1   # comp > amount × N → warning
GUARDRAIL_MAX_CONFIDENCE: float = 0.95           # confidence > N with fails → warning
GUARDRAIL_MIN_FAILS_FOR_WARNING: int = 2         # min policy failures to trigger warning

# ── RAG ─────────────────────────────────────────────────────────────────────
SIMILAR_CASES_SCORE_THRESHOLD: float = 0.40  # min cosine similarity for case results
SIMILAR_CASES_TOP_K: int = 5
POLICIES_TOP_K: int = 17                     # retrieve all; LLM filters relevance
POLICIES_SCORE_THRESHOLD: float = 0.0        # no floor — return everything, rank later
SEMANTIC_CACHE_THRESHOLD: float = 0.92       # min similarity to consider a cache hit
EMBEDDING_DIM: int = 1024                    # voyage-multilingual-2 (Voyage AI API)

# ── LLM ─────────────────────────────────────────────────────────────────────
LLM_TRUNCATION_LENGTH: int = 200    # chars to log to tracer (not to LLM)
LLM_DEFAULT_MAX_TOKENS: int = 4096
LLM_DEFAULT_MAX_RETRIES: int = 2
LLM_DEFAULT_TEMPERATURE: float = 0.3

# ── Judge ────────────────────────────────────────────────────────────────────
JUDGE_APPROVAL_THRESHOLD: float = 7.0    # overall_score >= N → approved
JUDGE_NEEDS_REVIEW_THRESHOLD: float = 5.0  # overall_score < N → needs_review flag
JUDGE_AUTO_INDEX_THRESHOLD: float = 8.0  # judge_score >= N → auto-index as precedent

# ── RAG / Retrieval ───────────────────────────────────────────────────────────
FRAUD_SCORE_DEFAULT: int = 50  # default fraud score when not provided
FRAUD_SCORE_HIGH_RISK_THRESHOLD: int = 30  # score < N → high risk query enrichment

# ── Pattern detection ─────────────────────────────────────────────────────────
MERCHANT_TIMEOUT_PATTERN_MIN_COUNT: int = 2  # MERCHANT_NO_RESPONSE events >= N → pattern

# ── LLM context limits ────────────────────────────────────────────────────────
LLM_MAX_CRITICAL_LOGS: int = 5  # max ERROR/WARN logs forwarded to LLM in summaries

# ── Feedback / auto-index ─────────────────────────────────────────────────────
FEEDBACK_MOTIVO_MAX_CHARS: int = 200     # max chars for motivo in auto-indexed cases
FEEDBACK_AUTO_RESOLUTION_DAYS: int = 1  # default resolution_days for auto-indexed cases
FEEDBACK_AUTO_ANALYST_TAG: str = "auto-index"  # analyst field for auto-indexed cases

# ── n8n Integration ──────────────────────────────────────────────────────────
N8N_WEBHOOK_PATH: str = "/webhook/chargeback-agent"
N8N_HEALTHZ_PATH: str = "/healthz"
N8N_TIMEOUT_S: float = 120.0
N8N_PING_TIMEOUT_S: float = 3.0

# ── Trace / Observability Names ──────────────────────────────────────────────
TRACE_RESOLVE: str = "resolve_chargeback"
TRACE_JUDGE: str = "judge_resolution"
TRACE_LLM_CALL: str = "llm_call"
TRACE_FEEDBACK: str = "analyst_feedback"
TRACE_FEEDBACK_SCORE: str = "analyst_feedback_judge_score"

# ── Feedback Response ────────────────────────────────────────────────────────
FEEDBACK_STATUS_RECORDED: str = "recorded"
FEEDBACK_CASE_ID_PREFIX: str = "FB"

# ── Fallback Values ──────────────────────────────────────────────────────────
FALLBACK_TX_ID: str = "unknown"

# ── SLA Types ────────────────────────────────────────────────────────────────
SLA_TYPE_VIP: str = "vip"
SLA_TYPE_EXTENDED: str = "extended"
SLA_TYPE_STANDARD: str = "standard"

# ── Health Status ────────────────────────────────────────────────────────────
HEALTH_OK: str = "ok"
HEALTH_HEALTHY: str = "healthy"
HEALTH_DEGRADED: str = "degraded"

# ── Conversion ───────────────────────────────────────────────────────────────
SECONDS_TO_MS: int = 1000
