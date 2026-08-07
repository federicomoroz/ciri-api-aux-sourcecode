"""
Pydantic models for API request/response validation.

Only models actively used by routes are defined here.
Data structures for LLM I/O (Resolution, PolicyVerdict, etc.) are documented
in docs/prompts.md and flow as plain dicts through the pipeline.
"""

from pydantic import BaseModel, ConfigDict, Field

from .context import CaseContext
from .enums import ResolutionOutcome, RiskLevel, Severity, VerdictType


class ResolveRequest(BaseModel):
    transaction_id: str = Field(min_length=1)
    tx_data: dict
    policies: list[dict]
    similar_cases: list[dict]
    logs: list[dict]
    merchant_risk: dict
    client_history: dict
    motivo: str | None = None
    cliente_vip: bool = False
    # `Sintetizar Resolucion` manda el contexto compilado entero, asi que esto
    # llega. Cuando el nodo enumeraba campos a mano no llegaba, Pydantic lo
    # descartaba en silencio y la llamada al SLA se pagaba para nada.
    sla: dict = {}

    def to_context(self) -> CaseContext:
        """Los nombres del contrato con n8n, traducidos al tipo interno."""
        return CaseContext(
            transaction=self.tx_data,
            motivo=self.motivo,
            cliente_vip=self.cliente_vip,
            logs=self.logs,
            policies=self.policies,
            similar_cases=self.similar_cases,
            merchant_risk=self.merchant_risk,
            client_history=self.client_history,
            sla=self.sla,
        )


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
    motivo: str | None = None  # motivo del contracargo, para indexar el precedente


class SLACheckRequest(BaseModel):
    case_open_date: str
    country: str
    cliente_vip: bool = False
    # Con el id, la ruta busca el caso historico y usa SUS fechas: la apertura
    # real del reclamo y, si esta cerrado, su cierre. Sin el id se mide desde
    # `case_open_date` hasta hoy, que es el caso de un reclamo recien abierto.
    transaction_id: str | None = None


class PolicyCreate(BaseModel):
    code: str = Field(min_length=1, pattern=r"^POL-[A-Z]{2,4}-\d{3}$")
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    # Lo que la politica HACE, junto a lo que la politica DICE. Estaba en
    # constants.py: se podia editar la descripcion sin deploy pero no la
    # capacidad de bloquear ni el plazo.
    #
    # `puede_bloquear` arranca en false a proposito: un veredicto BLOCKER frena
    # el caso sin revision humana, asi que habilitarlo tiene que ser un acto
    # explicito y no el resultado de olvidarse un campo.
    puede_bloquear: bool = False
    sla_dias: int | None = Field(default=None, gt=0)


class PolicyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    category: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    reference: str | None = Field(default=None, min_length=1)
    puede_bloquear: bool | None = None
    sla_dias: int | None = Field(default=None, gt=0)


class ModeloPasoUpdate(BaseModel):
    """Cambio de modelo para un paso del pipeline. Sin credenciales, a proposito."""
    proveedor: str = Field(min_length=1)
    modelo: str = Field(min_length=1)


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
    api_key: str | None = Field(default=None, exclude=True)
    # El toggle del panel manda su eleccion aca. Si no viene, decide el servidor.
    demo_mode: bool | None = None


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
    model_config = ConfigDict(extra="ignore")

    transaction_id: str = ""
    recommended_action: ResolutionOutcome
    confidence: float
    justification: str = ""
    risk_level: RiskLevel
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
    # True cuando la respuesta salio del analisis guardado en vez del modelo.
    # Viaja hasta el informe, que lo muestra como "DEMO (Caso prearmado)".
    demo: bool = False
    # Cuando el caso pedido no tenia analisis guardado, aca va SU id: esta
    # resolucion es de otra transaccion, servida como ejemplo. El informe lo
    # usa para responder con el ejemplo entero en vez de mezclar los dos.
    demo_ejemplo_de: str | None = None


class JudgeResponse(BaseModel):
    """Response from POST /api/analyze/judge."""
    overall_score: float
    criteria: dict[str, float] = {}
    approved: bool
    strengths: list[str] = []
    weaknesses: list[str] = []
    demo: bool = False


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


class AlertRequest(BaseModel):
    """Incoming alert from n8n error handler or resolve pipeline."""
    event_type: str = Field(min_length=1)
    severity: Severity = Severity.ERROR
    message: str = Field(min_length=1)
    source: str = ""
    transaction_id: str | None = None
    metadata: dict = {}


class AlertResponse(BaseModel):
    """Alert stored in SQLite."""
    id: int
    event_type: str
    severity: str
    message: str
    source: str
    transaction_id: str | None = None
    created_at: str = ""
