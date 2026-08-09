"""La decision de un analista, con sus tres reglas.

Esta logica vivia adentro del workflow de n8n, en un nodo Code. Los dos bugs que
el propio nodo documenta en sus comentarios —el `MODIFY` registrado como
aprobacion y el vencimiento que caia en `APPROVE`— no podian tener test ahi.
Ahora lo tienen: es la diferencia entre una invariante escrita en prosa y una
invariante fijada.
"""

import pytest

from api.app.domain import hitl
from api.app.domain.constants import (
    HITL_APROBADA,
    HITL_DECISION_SIN_RESPUESTA,
    HITL_MODIFICADA,
    HITL_RECHAZADA,
)
from api.app.domain.enums import ResolutionOutcome

RESOLUCION = {"recommended_action": "APPROVE", "confidence": 0.9}
TXN = "TXN-00042"


class TestFallaCerrado:
    """Sin decision no hay aprobacion. Es la regla que mas plata cuida."""

    def test_el_plazo_vencido_no_aprueba(self):
        d = hitl.normalizar_decision("", "")
        assert d["final_outcome"] == ResolutionOutcome.PENDING_HITL
        assert d["analyst_decision"] == HITL_DECISION_SIN_RESPUESTA
        assert d["timeout"] is True

    def test_solo_espacios_es_lo_mismo_que_nada(self):
        assert hitl.normalizar_decision("   ", "  ")["timeout"] is True

    def test_una_decision_que_no_existe_queda_pendiente(self):
        """Adivinar cual quiso decir seria decidir por el analista."""
        d = hitl.normalizar_decision("QUIZAS", "")
        assert d["final_outcome"] == ResolutionOutcome.PENDING_HITL
        # No es un vencimiento: alguien contesto, solo que algo que no se entiende.
        assert d["timeout"] is False
        assert d["analyst_decision"] == "QUIZAS"


class TestElMapeoDeLasTresOpciones:
    """El formulario ofrece tres. Mientras mapeo dos, MODIFY entraba como APPROVE."""

    @pytest.mark.parametrize(
        ("elegido", "esperado"),
        [
            ("APPROVE", HITL_APROBADA),
            ("approve", HITL_APROBADA),
            ("APPROVED", HITL_APROBADA),
            ("REJECT", HITL_RECHAZADA),
            ("REJECTED", HITL_RECHAZADA),
            ("MODIFY", HITL_MODIFICADA),
            ("MODIFIED", HITL_MODIFICADA),
        ],
    )
    def test_cada_opcion_da_su_resultado(self, elegido, esperado):
        assert hitl.normalizar_decision(elegido, "")["final_outcome"] == esperado

    def test_modificar_no_es_aprobar(self):
        """El bug concreto: con judge >= 8 se indexaba como precedente aprobado."""
        assert hitl.normalizar_decision("MODIFY", "")["final_outcome"] != HITL_APROBADA


class TestQueEntraAlCorpusDePrecedentes:
    """Solo se indexa lo que una persona avalo TAL CUAL."""

    def _feedback(self, decision: str) -> dict:
        return hitl.feedback_de(
            hitl.normalizar_decision(decision, "notas"),
            transaction_id=TXN, judge_score=9.0,
            resolution=RESOLUCION, motivo="Fraude",
        )

    def test_aprobada_viaja_la_resolucion(self):
        assert self._feedback("APPROVE")["resolution"] == RESOLUCION

    def test_modificada_no_viaja(self):
        """La aprobo con cambios que el sistema no tiene: guardarla seria guardar
        algo que nadie aprobo."""
        assert self._feedback("MODIFY")["resolution"] is None

    def test_rechazada_no_viaja(self):
        assert self._feedback("REJECT")["resolution"] is None

    def test_el_vencimiento_no_viaja(self):
        assert self._feedback("")["resolution"] is None

    def test_el_feedback_se_registra_igual(self):
        """Que no se indexe el precedente no significa que no quede constancia."""
        f = self._feedback("REJECT")
        assert f["transaction_id"] == TXN
        assert f["final_outcome"] == HITL_RECHAZADA
        assert f["judge_score"] == 9.0

    def test_el_motivo_vacio_viaja_como_none(self):
        """`FeedbackService` lo reemplaza por su default; una cadena vacia no."""
        f = hitl.feedback_de(
            hitl.normalizar_decision("APPROVE", ""), transaction_id=TXN,
            judge_score=9.0, resolution=RESOLUCION, motivo="",
        )
        assert f["motivo"] is None


@pytest.fixture
def client():
    """La ruta sola, sin el resto de la app.

    `/api/hitl/decide` no tiene dependencias: normaliza lo que recibe y devuelve.
    Montar solo su router evita levantar el lifespan entero —SQLite, Qdrant,
    clientes de modelo— para probar una funcion pura detras de HTTP.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.app.routes import hitl as ruta

    app = FastAPI()
    app.include_router(ruta.router)
    return TestClient(app)


class TestLaRuta:

    def test_devuelve_decision_y_feedback(self, client):
        r = client.post("/api/hitl/decide", json={
            "decision": "MODIFY", "notes": "bajar el monto",
            "transaction_id": TXN, "judge_score": 9.1, "resolution": RESOLUCION,
        })
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["hitl_decision"]["final_outcome"] == HITL_MODIFICADA
        assert cuerpo["feedback"]["resolution"] is None

    def test_sin_decision_es_valido_y_no_aprueba(self, client):
        """El plazo vencido llega asi: con el campo vacio, no ausente."""
        r = client.post("/api/hitl/decide", json={
            "decision": "", "transaction_id": TXN,
        })
        assert r.status_code == 200
        assert r.json()["hitl_decision"]["timeout"] is True

    def test_sin_transaccion_no_se_procesa(self, client):
        assert client.post("/api/hitl/decide", json={"decision": "APPROVE"}).status_code == 422
