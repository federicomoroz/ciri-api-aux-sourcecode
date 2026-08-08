from enum import StrEnum


class PaymentMethod(StrEnum):
    CREDIT_MC = "Credito MC"
    CREDIT_VISA = "Credito Visa"
    DEBIT_MC = "Debito MC"
    DEBIT_VISA = "Debito Visa"
    CRYPTO = "Cripto"
    BNPL = "BNPL"
    VIRTUAL_ACCOUNT = "Cuenta Virtual"


# Que cuenta como LATAM. Decide el plazo del SLA (`check_sla`), el enriquecimiento
# de la consulta al RAG (`QueryBuilder`) y la evaluacion de POL-EXC-004.
#
# Estaban los 7 del dataset. El prompt de evaluacion de politicas traia su propia
# lista de 20 escrita a mano, asi que para un ECU el codigo aplicaba plazo
# extendido de no-LATAM y el LLM leia que ECU si era LATAM y marcaba la politica
# como no aplicable: el informe mostraba las dos cosas en la misma pagina. Gana
# la lista larga, que ademas es la geograficamente correcta, y el prompt ahora
# la lee de aca.
LATAM_COUNTRIES: set[str] = {
    "ARG", "BOL", "BRA", "CHL", "COL", "CRI", "CUB", "DOM", "ECU", "GTM",
    "HND", "HTI", "MEX", "NIC", "PAN", "PER", "PRY", "SLV", "URY", "VEN",
}


class Channel(StrEnum):
    API = "API"
    APP_MOVIL = "App Movil"
    IVR = "IVR"
    POS = "POS"
    WEB = "Web"


class TransactionStatus(StrEnum):
    APROBADA = "Aprobada"
    CONTRACARGO_INICIADO = "Contracargo iniciado"
    EN_DISPUTA = "En disputa"
    PENDIENTE_REVISION = "Pendiente revision"
    RECHAZADA = "Rechazada"
    RESUELTA = "Resuelta"


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class RiskLevel(StrEnum):
    BLOCKER = "BLOCKER"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class VerdictType(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKER = "BLOCKER"
    WARNING = "WARNING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ResolutionOutcome(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"
    PENDING_HITL = "PENDING_HITL"


class MerchantFlag(StrEnum):
    SUSPENDED_MERCHANT = "suspended_merchant"
    HIGH_CB_RATIO = "high_cb_ratio"


class ClientFlag(StrEnum):
    RECIDIVIST = "recidivist"
    GEO_ANOMALY = "geo_anomaly"


class ErrorPattern(StrEnum):
    SYSTEMATIC_MERCHANT_TIMEOUT = "systematic_merchant_timeout"
    CONNECTIVITY_ISSUE = "connectivity_issue"
    BLOCKED_FOR_FRAUD = "blocked_for_fraud"
    DUPLICATE_CHARGE = "duplicate_charge"
    SLA_VIOLATION = "sla_violation"
    INTEGRATION_FAILURE = "integration_failure"
    SESSION_INTERRUPTED_PAYMENT = "session_interrupted_payment"
    GEOGRAPHIC_ANOMALY = "geographic_anomaly"


class LogEventType(StrEnum):
    AUDIT_LOG = "AUDIT_LOG"
    AUTH_APPROVED = "AUTH_APPROVED"
    AUTH_DECLINED = "AUTH_DECLINED"
    AUTH_REQUEST = "AUTH_REQUEST"
    CHARGEBACK_OPENED = "CHARGEBACK_OPENED"
    CHARGEBACK_UPDATED = "CHARGEBACK_UPDATED"
    DOUBLE_CHARGE_DETECT = "DOUBLE_CHARGE_DETECT"
    FRAUD_ALERT = "FRAUD_ALERT"
    FRAUD_CHECK = "FRAUD_CHECK"
    GEO_ANOMALY = "GEO_ANOMALY"
    MERCHANT_NOTIFIED = "MERCHANT_NOTIFIED"
    MERCHANT_NO_RESPONSE = "MERCHANT_NO_RESPONSE"
    PAYMENT_COMPLETED = "PAYMENT_COMPLETED"
    PAYMENT_INITIATED = "PAYMENT_INITIATED"
    REFUND_PROCESSED = "REFUND_PROCESSED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SLA_BREACH = "SLA_BREACH"
    TIMEOUT_RETRY = "TIMEOUT_RETRY"
    WEBHOOK_FAILED = "WEBHOOK_FAILED"
    WEBHOOK_SENT = "WEBHOOK_SENT"
