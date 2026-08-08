"""Golden master del nucleo de decision: la red que sostiene el refactor SOLID.

Este archivo no prueba que las decisiones sean *correctas* — de eso se ocupan
`test_guardrails.py` y `test_services.py`, que afirman reglas concretas con su
razon. Prueba algo distinto y complementario: que **no cambien**.

Se recorre una matriz de entradas por cada funcion del nucleo determinista y se
compara la salida contra un archivo grabado. Cualquier diferencia falla, incluida
la que nadie buscó. Es lo que hace falta para mover quince funciones de un modulo
a otro con confianza: los tests de reglas dicen que lo que se afirmaba sigue
siendo cierto, y este dice que lo que *no* se afirmaba tampoco se movio.

Si hay que actualizar el archivo grabado es porque una decision de negocio cambio
a proposito — y entonces el commit tiene que decir cual:

    python tests/unit/test_nucleo_no_cambia.py
"""

import json
from pathlib import Path

import pytest

from api.app.domain.enums import ResolutionOutcome, RiskLevel, VerdictType
from api.app.services.resolution import ResolutionService

GRABADO = Path(__file__).parent / "nucleo_grabado.json"


# ── La matriz ────────────────────────────────────────────────────────────────
# Cada caso lleva un nombre que dice que situacion representa, para que un diff
# del archivo grabado se lea como una lista de escenarios y no de indices.

def _v(code: str, verdict: str, humano: bool = False) -> dict:
    return {"policy_code": code, "verdict": verdict, "requires_human_review": humano}


VEREDICTOS = {
    "sin_veredictos": [],
    "todo_pass": [_v("POL-CB-001", VerdictType.PASS), _v("POL-CB-002", VerdictType.PASS)],
    "un_warning": [_v("POL-CB-001", VerdictType.PASS), _v("POL-FRD-002", VerdictType.WARNING)],
    "un_fail": [_v("POL-CB-001", VerdictType.PASS), _v("POL-FRD-001", VerdictType.FAIL)],
    "dos_fails": [_v("POL-FRD-001", VerdictType.FAIL), _v("POL-CB-005", VerdictType.FAIL)],
    "un_blocker": [_v("POL-EXC-003", VerdictType.BLOCKER)],
    "blocker_y_fail": [_v("POL-EXC-003", VerdictType.BLOCKER), _v("POL-FRD-001", VerdictType.FAIL)],
    "pide_humano": [_v("POL-CB-001", VerdictType.PASS, humano=True)],
    "ilegible": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKED"}],
    "ilegible_entre_validos": [
        _v("POL-CB-001", VerdictType.PASS), {"policy_code": "POL-FRD-001", "verdict": "FAILED"},
    ],
    "no_aplica": [_v("POL-EXC-002", VerdictType.NOT_APPLICABLE)],
}

TRANSACCIONES = {
    "score_severo": {"id": "TXN-A", "fraud_score": 8, "amount_usd": 2095.9,
                     "payment_method": "Cripto", "country": "ARG", "merchant": "Airbnb"},
    "score_medio": {"id": "TXN-B", "fraud_score": 45, "amount_usd": 300.0,
                    "payment_method": "Tarjeta de credito", "country": "BRA", "merchant": "Uber"},
    "score_alto": {"id": "TXN-C", "fraud_score": 92, "amount_usd": 12.5,
                   "payment_method": "Transferencia", "country": "USA", "merchant": "Booking.com"},
    "sin_score": {"id": "TXN-D", "amount_usd": 0.0, "merchant": "Spotify"},
}

SLAS = {
    "sin_reclamo": {"within_sla": None, "sin_reclamo_registrado": True},
    "en_plazo": {"within_sla": True, "days_elapsed": 4, "sla_limit_days": 10},
    "vencido": {"within_sla": False, "days_elapsed": 31, "sla_limit_days": 10},
    "vacio": {},
}

PROPUESTAS = {
    "approve": {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW,
                "confidence": 0.95, "compensation_applicable": False},
    "reject": {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER,
               "confidence": 0.8, "compensation_applicable": True},
    "hitl": {"recommended_action": ResolutionOutcome.PENDING_HITL, "risk_level": RiskLevel.MEDIUM,
             "confidence": 0.5, "compensation_applicable": False},
}

def _log(event: str, severity: str, detail: str = "detalle", code: str = "500") -> dict:
    return {"timestamp": "2024-09-23 10:00:00", "event": event, "service": "PaymentService",
            "code": code, "detail": detail, "severity": severity}


LOGS = {
    "sin_logs": [],
    "solo_info": [_log("AUDIT_LOG", "INFO", "auditado", "200")],
    "con_error": [
        _log("PAYMENT_FAILED", "ERROR", "gateway rechazo la operacion"),
        _log("MERCHANT_NO_RESPONSE", "WARN", "sin respuesta del comercio", "408"),
    ],
    # Dispara el patron de timeout sistematico (>= 2 MERCHANT_NO_RESPONSE).
    "timeout_sistematico": [
        _log("MERCHANT_NO_RESPONSE", "WARN", "sin respuesta", "408"),
        _log("MERCHANT_NO_RESPONSE", "WARN", "sin respuesta", "408"),
        _log("MERCHANT_NO_RESPONSE", "ERROR", "sin respuesta", "408"),
    ],
}

CASOS_SIMILARES = {
    "ninguno": [],
    "mismo_merchant": [
        {"motivo": "No reconoce la compra", "resolution": "Reembolsado", "merchant": "Airbnb",
         "score": 0.91, "resolution_days": 6, "case_id": "CB-0001"},
    ],
    "mezcla": [
        {"motivo": "Fraude con tarjeta robada", "resolution": "Rechazado", "merchant": "Uber",
         "score": 0.73, "resolution_days": 12, "case_id": "CB-0002"},
        {"motivo": "No reconoce la compra", "resolution": "Reembolsado parcial", "merchant": "Airbnb",
         "score": 0.55, "resolution_days": 3, "case_id": "CB-0003"},
    ],
}


def _matriz() -> dict:
    """Toda la salida del nucleo, para toda la matriz. Ordenada y serializable."""
    salida: dict[str, dict] = {}

    salida["determine_outcome"] = {
        f"{nv}|{nt}": ResolutionService._determine_outcome(v, t)
        for nv, v in VEREDICTOS.items()
        for nt, t in TRANSACCIONES.items()
    }

    salida["determine_compensation"] = {
        f"{ns}|{nt}": ResolutionService._determine_compensation(s, t)
        for ns, s in SLAS.items()
        for nt, t in TRANSACCIONES.items()
    }

    salida["detect_divergence"] = {
        f"{np_}|{nv}|{nt}": ResolutionService._detect_divergence(
            p, ResolutionService._determine_outcome(v, t), v,
        )
        for np_, p in PROPUESTAS.items()
        for nv, v in VEREDICTOS.items()
        for nt, t in TRANSACCIONES.items()
    }

    salida["validate_resolution"] = {
        f"{np_}|{nv}|{nt}": ResolutionService._validate_resolution(
            {**p, "policy_verdicts": v,
             "compensation_amount_usd": t.get("amount_usd", 0) * 1.5},
            t,
        )
        for np_, p in PROPUESTAS.items()
        for nv, v in VEREDICTOS.items()
        for nt, t in TRANSACCIONES.items()
    }

    salida["summarize_logs"] = {
        nl: ResolutionService._summarize_logs(lg) for nl, lg in LOGS.items()
    }

    salida["build_precedent_summary"] = {
        f"{nc}|{motivo}|{merchant}": ResolutionService._build_precedent_summary(c, motivo, merchant)
        for nc, c in CASOS_SIMILARES.items()
        for motivo in ("No reconoce la compra", "Cargo duplicado", "")
        for merchant in ("Airbnb", "Otro")
    }

    salida["codigos"] = {nv: ResolutionService._codigos(v) for nv, v in VEREDICTOS.items()}

    salida["extraer_propuesta"] = {
        np_: ResolutionService._extraer_propuesta(dict(p)) for np_, p in PROPUESTAS.items()
    }

    salida["clasificar_resolucion"] = {
        r or "(vacio)": list(ResolutionService._clasificar_resolucion(r))
        for r in ("Reembolsado", "Rechazado", "Reembolsado parcial", "Escalado", "", "Cualquier cosa")
    }

    return salida


# Los nombres, sin correr la matriz: `parametrize` se evalua al importar el
# modulo y recorrerla entera ahi cuesta y ensucia la salida con los logs del
# propio codigo. `test_estan_todas_las_funciones` verifica que esta lista no se
# quede corta.
FUNCIONES = (
    "build_precedent_summary", "clasificar_resolucion", "codigos",
    "determine_compensation", "determine_outcome", "detect_divergence",
    "extraer_propuesta", "summarize_logs", "validate_resolution",
)


def _serializable(m: dict) -> dict:
    # `default=str` porque los StrEnum serializan a su valor y las tuplas a lista:
    # lo que importa es que la comparacion sea estable, no el tipo exacto.
    return json.loads(json.dumps(m, sort_keys=True, default=str))


@pytest.fixture(scope="module")
def actual() -> dict:
    return _serializable(_matriz())


@pytest.fixture(scope="module")
def grabado() -> dict:
    assert GRABADO.exists(), (
        f"no hay golden master. Generalo con: python {Path(__file__).as_posix()}"
    )
    return json.loads(GRABADO.read_text(encoding="utf-8"))


class TestElNucleoDeDecisionNoCambia:
    """Una diferencia aca es una decision de negocio que cambio.

    Si el cambio fue a proposito, se regraba y el commit dice cual fue. Si no,
    el refactor rompio algo que ningun test de regla estaba mirando.
    """

    def test_estan_todas_las_funciones(self, actual, grabado):
        assert set(actual) == set(grabado), (
            "cambio el conjunto de funciones del nucleo: "
            f"faltan {set(grabado) - set(actual)}, sobran {set(actual) - set(grabado)}"
        )

    def test_la_lista_de_funciones_cubre_la_matriz(self, actual):
        """Sin esto, agregar una funcion al nucleo y olvidarla aca pasa en verde."""
        assert set(FUNCIONES) == set(actual)

    @pytest.mark.parametrize("funcion", FUNCIONES)
    def test_la_salida_es_la_misma(self, funcion, actual, grabado):
        esperado = grabado.get(funcion, {})
        obtenido = actual[funcion]
        distintos = {
            k: {"grabado": esperado.get(k), "ahora": v}
            for k, v in obtenido.items()
            if esperado.get(k) != v
        }
        faltantes = set(esperado) - set(obtenido)
        assert not distintos and not faltantes, (
            f"{funcion}: {len(distintos)} salidas cambiaron, {len(faltantes)} casos "
            f"desaparecieron\n{json.dumps(distintos, indent=2, ensure_ascii=False)[:2000]}"
        )


if __name__ == "__main__":  # pragma: no cover
    GRABADO.write_text(
        json.dumps(_serializable(_matriz()), indent=1, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"grabado: {GRABADO}")
