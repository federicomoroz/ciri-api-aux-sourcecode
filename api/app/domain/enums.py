from enum import StrEnum


class PaymentMethod(StrEnum):
    CREDIT_MC = "Credito MC"
    CREDIT_VISA = "Credito Visa"
    DEBIT_MC = "Debito MC"
    DEBIT_VISA = "Debito Visa"
    CRYPTO = "Cripto"
    BNPL = "BNPL"
    VIRTUAL_ACCOUNT = "Cuenta Virtual"


class Country(StrEnum):
    ARG = "ARG"
    BRA = "BRA"
    CHL = "CHL"
    COL = "COL"
    MEX = "MEX"
    PER = "PER"
    URY = "URY"
    USA = "USA"


LATAM_COUNTRIES: set[str] = {"ARG", "BRA", "CHL", "COL", "MEX", "PER", "URY"}


class Channel(StrEnum):
    API = "API"
    APP_MOVIL = "App Movil"
    IVR = "IVR"
    POS = "POS"
    WEB = "Web"


class Device(StrEnum):
    CHROME_WIN = "Chrome/Win"
    DESKTOP_LINUX = "Desktop/Linux"
    FIREFOX_MAC = "Firefox/Mac"
    HUAWEI_P50 = "Huawei P50"
    SAMSUNG_S23 = "Samsung S23"
    IPAD = "iPad"
    IPHONE_14 = "iPhone 14"


class TransactionStatus(StrEnum):
    APROBADA = "Aprobada"
    CONTRACARGO_INICIADO = "Contracargo iniciado"
    EN_DISPUTA = "En disputa"
    PENDIENTE_REVISION = "Pendiente revision"
    RECHAZADA = "Rechazada"
    RESUELTA = "Resuelta"


class CaseMotivo(StrEnum):
    CANCELACION_NO_PROCESADA = "Cancelacion no procesada"
    CARGO_DUPLICADO = "Cargo duplicado"
    CARGO_POST_CANCELACION = "Cargo post-cancelacion"
    DEFECTO_PRODUCTO = "Defecto de producto"
    FRAUDE_TARJETA_ROBADA = "Fraude con tarjeta robada"
    MONTO_INCORRECTO = "Monto incorrecto"
    NO_RECONOCE = "No reconoce la compra"
    PRODUCTO_NO_RECIBIDO = "Producto no recibido"
    SERVICIO_NO_PRESTADO = "Servicio no prestado"
    SUSCRIPCION_NO_AUTORIZADA = "Suscripcion no autorizada"


class CaseResolution(StrEnum):
    FAVOR_CLIENTE = "A favor del cliente"
    FAVOR_COMERCIO = "A favor del comercio"
    CERRADO_SIN_RESOLUCION = "Caso cerrado sin resolucion"
    EN_ESCALACION = "En escalacion"
    REEMBOLSO_PARCIAL = "Reembolso parcial"


class PolicyCategory(StrEnum):
    FRAUDE = "FRAUDE"
    CHARGEBACK = "CHARGEBACK"
    SLA = "SLA"
    EXCEPCION = "EXCEPCIÓN"


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
