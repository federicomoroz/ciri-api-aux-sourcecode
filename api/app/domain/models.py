"""
Pydantic models for API request/response validation.

Only models actively used by routes are defined here.
Data structures for LLM I/O (Resolution, PolicyVerdict, etc.) are documented
in docs/prompts.md and flow as plain dicts through the pipeline.
"""

from pydantic import BaseModel, ConfigDict, Field

from .enums import ResolutionOutcome, RiskLevel, VerdictType


class ResolveRequest(BaseModel):
    transaction_id: str = Field(min_length=1)
    agent_analysis: str
    tx_data: dict
    policies: list[dict]
    similar_cases: list[dict]
    logs: list[dict]
    merchant_risk: dict
    client_history: dict
    motivo: str | None = None
    cliente_vip: bool = False


class JudgeRequest(BaseModel):
    resolution: dict
    full_context: dict


class FeedbackRequest(BaseModel):
    transaction_id: str = Field(min_length=1)
    analyst_decision: str = Field(min_length=1)
    analyst_notes: str
    final_outcome: str = Field(min_length=1)
    judge_score: float = Field(ge=0.0, le=10.0)
    resolution: dict | None = None


class SLACheckRequest(BaseModel):
    case_open_date: str
    country: str
    cliente_vip: bool = False


class PolicyCreate(BaseModel):
    code: str = Field(min_length=1, pattern=r"^POL-[A-Z]{2,4}-\d{3}$")
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reference: str = Field(min_length=1)


class PolicyUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    reference: str | None = None


class ReportRequest(BaseModel):
    transaction: dict
    resolution: dict
    judge_evaluation: dict
    agent_analysis: str
    merchant_risk: dict
    client_profile: dict
    logs: list[dict]
    policies_evaluated: list[dict]
    similar_cases: list[dict]
    hitl_decision: dict | None = None
    cache_hit: bool = False
    guardrail_warnings: list[str] = []
    motivo: str | None = None
    cliente_vip: bool = False


class AnalyzeRequest(BaseModel):
    """Used by the test panel for direct pipeline analysis."""
    transaction_id: str = Field(min_length=1)
    motivo: str = Field(min_length=1)
    cliente_vip: bool = False


# ---- LLM Output Validation Models ----
# These validate the structure of JSON returned by LLM calls.
# extra="ignore" tolerates unexpected fields from the LLM.
# All fields have defaults so partial responses don't crash.


class PolicyVerdictOutput(BaseModel):
    """Validated LLM output from v1_policy_eval."""
    model_config = ConfigDict(extra="ignore")

    policy_code: str
    verdict: VerdictType
    reasoning: str = ""
    requires_human_review: bool = False


class ResolutionOutput(BaseModel):
    """Validated LLM output from v1_resolution."""
    model_config = ConfigDict(extra="ignore")

    transaction_id: str = ""
    recommended_action: ResolutionOutcome
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    justification: str = ""
    policy_verdicts: list[dict] = []
    precedent_summary: str = ""
    log_summary: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    compensation_applicable: bool = False
    compensation_amount_usd: float = Field(ge=0.0, default=0.0)
    next_steps: list[str] = []
    requires_hitl: bool = False
    hitl_reason: str | None = None


class JudgeEvaluationOutput(BaseModel):
    """Validated LLM output from v1_judge."""
    model_config = ConfigDict(extra="ignore")

    overall_score: float = Field(ge=0.0, le=10.0, default=0.0)
    criteria: dict[str, float] = {}
    approved: bool = False
    strengths: list[str] = []
    weaknesses: list[str] = []


# ---- API Response Models ----
# Typed responses for OpenAPI documentation and contract validation.


class ResolveResponse(BaseModel):
    """Response from POST /api/analyze/resolve."""
    model_config = ConfigDict(extra="allow")

    transaction_id: str = ""
    recommended_action: str
    confidence: float
    justification: str = ""
    risk_level: str
    policy_verdicts: list[dict] = []
    precedent_summary: str = ""
    log_summary: str = ""
    compensation_applicable: bool = False
    compensation_amount_usd: float = 0.0
    next_steps: list[str] = []
    requires_hitl: bool = False
    hitl_reason: str | None = None
    guardrail_warnings: list[str] = []
    trace_id: str = ""


class JudgeResponse(BaseModel):
    """Response from POST /api/analyze/judge."""
    overall_score: float
    criteria: dict[str, float] = {}
    approved: bool
    strengths: list[str] = []
    weaknesses: list[str] = []


class FeedbackResponse(BaseModel):
    """Response from POST /api/feedback."""
    status: str
    feedback_id: int
    auto_indexed: bool
    needs_review: bool
    judge_score: float


class HealthResponse(BaseModel):
    """Response from GET /health."""
    status: str
    sqlite: str
    qdrant: str
    collections: dict[str, int] = {}


class DashboardResponse(BaseModel):
    """Response from GET /api/analytics/dashboard."""
    total_transactions: int
    total_cases: int
    total_feedback: int
    avg_judge_score: float
    auto_indexed_count: int
    top_merchants_by_chargebacks: list[dict]
    transactions_by_country: list[dict]
    transactions_by_payment_method: list[dict]


class LangfuseStatsResponse(BaseModel):
    """Response from GET /api/langfuse/stats."""
    enabled: bool
    summary: dict | None = None
    recent_traces: list[dict] = []
