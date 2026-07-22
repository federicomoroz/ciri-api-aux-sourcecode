from datetime import datetime, timezone
from pydantic import BaseModel, Field

from .enums import RiskLevel, ResolutionOutcome, Severity, VerdictType


class Transaction(BaseModel):
    id: str
    client_id: str
    merchant: str
    amount_usd: float
    date: str
    payment_method: str
    country: str
    channel: str
    device: str
    fraud_score: int
    status: str
    notes: str | None = None


class HistoricalCase(BaseModel):
    case_id: str
    transaction_id: str
    motivo: str
    resolution: str
    resolution_days: int
    analyst: str
    observations: str | None = None
    open_date: str
    close_date: str


class Policy(BaseModel):
    code: str
    name: str
    category: str
    description: str
    reference: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LogEvent(BaseModel):
    timestamp: str
    transaction_id: str
    event: str
    service: str
    code: str
    detail: str
    severity: Severity


class PolicyVerdict(BaseModel):
    policy_code: str
    verdict: VerdictType
    reasoning: str
    requires_human_review: bool = False


class Resolution(BaseModel):
    transaction_id: str
    recommended_action: ResolutionOutcome
    confidence: float
    justification: str
    policy_verdicts: list[PolicyVerdict]
    precedent_summary: str
    log_summary: str
    risk_level: RiskLevel
    compensation_applicable: bool = False
    compensation_amount_usd: float = 0.0
    next_steps: list[str] = []
    requires_hitl: bool = False
    hitl_reason: str | None = None


class JudgeEvaluation(BaseModel):
    overall_score: float
    criteria: dict[str, float]
    approved: bool
    strengths: list[str]
    weaknesses: list[str]


class AnalyzeRequest(BaseModel):
    transaction_id: str
    motivo: str | None = None
    fecha_reclamo: str | None = None
    cliente_vip: bool = False
    docs_completa: bool = False


class ResolveRequest(BaseModel):
    transaction_id: str
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
    transaction_id: str
    analyst_decision: str
    analyst_notes: str
    final_outcome: str
    judge_score: float
    resolution: dict | None = None


class SLACheckRequest(BaseModel):
    case_open_date: str
    country: str
    cliente_vip: bool = False


class SLACheckResponse(BaseModel):
    within_sla: bool
    days_elapsed: int
    sla_limit_days: int
    sla_type: str
    policy_reference: str
    compensation_applicable: bool = False


class MerchantRiskProfile(BaseModel):
    merchant: str
    total_transactions: int
    total_chargebacks: int
    cb_ratio: float
    total_volume_usd: float
    avg_transaction_usd: float
    flags: list[str]
    is_strategic: bool


class ClientProfile(BaseModel):
    client_id: str
    total_transactions: int
    total_chargebacks: int
    rejected_transactions: int
    countries_used: list[str]
    payment_methods_used: list[str]
    flags: list[str]


class PolicyCreate(BaseModel):
    code: str
    name: str
    category: str
    description: str
    reference: str


class PolicyUpdate(BaseModel):
    name: str | None = None
    category: str | None = None
    description: str | None = None
    reference: str | None = None


class CacheLookupRequest(BaseModel):
    transaction_id: str
    motivo: str | None = None
    cliente_vip: bool = False


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
