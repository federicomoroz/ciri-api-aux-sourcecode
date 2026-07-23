import pytest

from api.app.llm.client import LLMResult


class MockLLMClient:
    """Test double for LLM calls. Returns pre-configured responses matched by keyword in system prompt."""

    def __init__(self, responses: dict[str, str] | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def complete(self, system: str, user: str, **kwargs) -> LLMResult:
        self.calls.append({"system": system, "user": user})
        for keyword, response in self.responses.items():
            if keyword.lower() in system.lower():
                return LLMResult(text=response, input_tokens=100, output_tokens=50)
        return LLMResult(
            text='{"error": "no mock response configured for this system prompt"}',
            input_tokens=10,
            output_tokens=10,
        )


# ---- Sample data fixtures ----

@pytest.fixture
def sample_transaction_blocker() -> dict:
    """TXN-00051: Crypto + fraud_score=8 → BLOCKER scenario."""
    return {
        "id": "TXN-00051",
        "client_id": "CLI-0003",
        "merchant": "Airbnb",
        "amount_usd": 2095.90,
        "date": "2024-09-23",
        "payment_method": "Cripto",
        "country": "COL",
        "channel": "POS",
        "device": "Firefox/Mac",
        "fraud_score": 8,
        "status": "Contracargo iniciado",
        "notes": "Alto riesgo detectado",
    }


@pytest.fixture
def sample_transaction_hitl() -> dict:
    """TXN-00042: Credit Visa + score=4 + HIGH → HITL scenario."""
    return {
        "id": "TXN-00042",
        "client_id": "CLI-0036",
        "merchant": "Airbnb",
        "amount_usd": 2055.76,
        "date": "2024-07-15",
        "payment_method": "Credito Visa",
        "country": "BRA",
        "channel": "Web",
        "device": "Chrome/Win",
        "fraud_score": 4,
        "status": "En disputa",
        "notes": None,
    }


@pytest.fixture
def sample_logs() -> list[dict]:
    """Sample logs with anomaly patterns for TXN-00051."""
    return [
        {
            "timestamp": "2024-09-23 10:00:00",
            "transaction_id": "TXN-00051",
            "event": "MERCHANT_NO_RESPONSE",
            "service": "IntegrationBus",
            "code": "408",
            "detail": "Timeout al contactar comercio",
            "severity": "ERROR",
        },
        {
            "timestamp": "2024-09-23 10:01:00",
            "transaction_id": "TXN-00051",
            "event": "MERCHANT_NO_RESPONSE",
            "service": "IntegrationBus",
            "code": "408",
            "detail": "Segundo intento fallido",
            "severity": "ERROR",
        },
        {
            "timestamp": "2024-09-23 10:02:00",
            "transaction_id": "TXN-00051",
            "event": "FRAUD_ALERT",
            "service": "FraudEngine",
            "code": "200",
            "detail": "Score 8/100 — alto riesgo",
            "severity": "WARN",
        },
        {
            "timestamp": "2024-09-23 10:03:00",
            "transaction_id": "TXN-00051",
            "event": "SESSION_EXPIRED",
            "service": "AuthService",
            "code": "401",
            "detail": "Sesion expirada",
            "severity": "WARN",
        },
        {
            "timestamp": "2024-09-23 10:04:00",
            "transaction_id": "TXN-00051",
            "event": "WEBHOOK_FAILED",
            "service": "NotifyService",
            "code": "500",
            "detail": "Error al notificar al comercio",
            "severity": "ERROR",
        },
    ]


@pytest.fixture
def sample_policies() -> list[dict]:
    """Minimal set of policies for testing."""
    return [
        {
            "code": "POL-FRD-001",
            "name": "Score minimo de aprobacion",
            "category": "FRAUDE",
            "description": "Toda transaccion con score antifraude inferior a 30 debe ser rechazada.",
            "reference": "Manual de Riesgo v3.2, Sec. 4.1",
        },
        {
            "code": "POL-EXC-003",
            "name": "Transacciones con Criptomonedas",
            "category": "EXCEPCION",
            "description": "Las transacciones con criptomonedas no son reversibles. No se aceptan contracargos bajo ninguna circunstancia. BLOCKER absoluto.",
            "reference": "Manual de Excepciones v1.0, Sec. 4",
        },
        {
            "code": "POL-SLA-002",
            "name": "Tiempo de resolucion",
            "category": "SLA",
            "description": "El tiempo maximo de resolucion es 10 dias habiles para LATAM.",
            "reference": "SLA Agreement v2.1, Art. 3",
        },
    ]


# ---- Mock LLM ----

@pytest.fixture
def mock_llm_blocker():
    """MockLLMClient configured for BLOCKER scenario responses."""
    return MockLLMClient(responses={
        "auditor de cumplimiento": (
            '[{"policy_code":"POL-EXC-003","verdict":"BLOCKER",'
            '"reasoning":"Pago con Cripto — contracargo imposible segun POL-EXC-003","requires_human_review":false},'
            '{"policy_code":"POL-FRD-001","verdict":"FAIL",'
            '"reasoning":"Score 8 inferior al minimo de 30","requires_human_review":false}]'
        ),
        "analista senior": (
            '{"transaction_id":"TXN-00051","recommended_action":"REJECT","confidence":0.99,'
            '"justification":"BLOCKER por POL-EXC-003 y FAIL por POL-FRD-001 (score=8).","policy_verdicts":[],'
            '"precedent_summary":"Sin precedentes relevantes.","log_summary":"MERCHANT_NO_RESPONSE x2, FRAUD_ALERT.",'
            '"risk_level":"BLOCKER","compensation_applicable":false,"compensation_amount_usd":0.0,'
            '"next_steps":["Notificar al cliente que las transacciones cripto no son reversibles",'
            '"Cerrar el caso con resolucion: a favor del comercio"],'
            '"requires_hitl":false,"hitl_reason":null}'
        ),
        "supervisor de calidad": (
            '{"overall_score":9.2,"criteria":{"policy_consistency":10.0,"justification_quality":9.0,'
            '"precedent_usage":8.0,"risk_assessment":9.5,"actionability":9.5},'
            '"approved":true,"strengths":["Aplicacion correcta de POL-EXC-003 como BLOCKER"],'
            '"weaknesses":[]}'
        ),
    })


# ---- In-memory SQLite ----

@pytest.fixture
def in_memory_db_path(tmp_path, sample_transaction_blocker, sample_transaction_hitl, sample_policies):
    """SQLite DB in temp dir, pre-populated with sample data."""
    from api.app.data.loader import init_sqlite

    data = {
        "transactions": [sample_transaction_blocker, sample_transaction_hitl],
        "cases": [
            {
                "case_id": "CB-TEST-001",
                "transaction_id": "TXN-00051",
                "motivo": "No reconoce la compra",
                "resolution": "A favor del comercio",
                "resolution_days": 5,
                "analyst": "Test Analyst",
                "observations": "Transaccion con cripto confirmada",
                "open_date": "2024-09-23",
                "close_date": "2024-09-28",
            }
        ],
        "policies": sample_policies,
        "logs": [],
    }

    db_path = str(tmp_path / "test.db")
    init_sqlite(db_path, data)
    return db_path
