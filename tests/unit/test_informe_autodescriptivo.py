"""El informe se lleva sus propios datos adentro.

Por qué existe: los informes de los casos demo se generaron cuando había saldo,
y cuando hizo falta el JSON de la resolución no estaba en ningún lado — hubo que
recuperarlo parseando el HTML renderizado. Un informe que carga sus datos no
vuelve a dejar a nadie en esa posición.
"""

import json
import re

import pytest

from api.app.reports.generator import ReportGenerator

BLOQUE = re.compile(
    r'<script type="application/json" id="datos-del-caso">(.*?)</script>', re.S
)

BASE = {
    "transaction": {
        "id": "TXN-00051", "merchant": "Airbnb", "date": "2024-09-23",
        "amount_usd": 2095.9, "client_id": "CLI-0003", "payment_method": "Cripto",
        "country": "COL", "channel": "POS", "device": "d", "status": "s",
        "fraud_score": 8, "notes": "",
    },
    "resolution": {
        "risk_level": "BLOCKER", "recommended_action": "REJECT", "confidence": 0.95,
        "justification": "Cripto es irreversible",
        "policy_verdicts": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "r"}],
        "next_steps": ["Notificar al cliente"],
    },
    "judge_evaluation": {"overall_score": 8.6, "approved": True, "criteria": {"x": 9.0}},
    "agent_analysis": "",
    "merchant_risk": {
        "merchant": "Airbnb", "total_transactions": 4, "total_chargebacks": 3,
        "cb_ratio": 0.75, "total_volume_usd": 5521.08, "flags": [], "is_strategic": False,
    },
    "client_profile": {
        "client_id": "CLI-0003", "total_transactions": 4, "total_chargebacks": 0,
        "rejected_transactions": 0, "countries_used": ["COL"],
        "payment_methods_used": ["Cripto"], "flags": [],
    },
    "logs": [], "policies_evaluated": [], "similar_cases": [], "guardrail_warnings": [],
}


def _datos_del(html: str) -> dict:
    """Lo mismo que haría quien quiera reingerir un informe."""
    m = BLOQUE.search(html)
    assert m, "el informe no trae el bloque de datos"
    return json.loads(m.group(1))


@pytest.fixture
def html() -> str:
    return ReportGenerator().render(dict(BASE))


class TestSeRecuperaTodo:
    def test_la_resolucion_vuelve_entera(self, html):
        r = _datos_del(html)["resolution"]
        assert r["recommended_action"] == "REJECT"
        assert r["risk_level"] == "BLOCKER"
        assert r["confidence"] == 0.95

    def test_los_veredictos_conservan_su_estructura(self, html):
        v = _datos_del(html)["resolution"]["policy_verdicts"]
        assert v == BASE["resolution"]["policy_verdicts"]

    def test_la_evaluacion_del_juez_vuelve_entera(self, html):
        assert _datos_del(html)["judge_evaluation"] == BASE["judge_evaluation"]

    def test_el_contexto_tambien_esta(self, html):
        d = _datos_del(html)
        assert d["transaction"]["id"] == "TXN-00051"
        assert d["merchant_risk"]["cb_ratio"] == 0.75


class TestNoRompeElHtml:
    def test_un_texto_con_script_no_corta_la_etiqueta(self):
        """Sin escapar '</', un dato con '</script>' partiria el bloque al medio."""
        datos = dict(BASE)
        datos["resolution"] = {**BASE["resolution"], "justification": "esto </script> adentro"}
        recuperado = _datos_del(ReportGenerator().render(datos))
        assert recuperado["resolution"]["justification"] == "esto </script> adentro"

    def test_conserva_los_acentos(self):
        datos = dict(BASE)
        datos["resolution"] = {**BASE["resolution"], "justification": "violación de política"}
        assert "violación" in _datos_del(ReportGenerator().render(datos))["resolution"]["justification"]

    def test_un_dato_no_serializable_no_tumba_el_informe(self):
        """Una fecha suelta en el contexto no puede impedir que se genere el informe."""
        from datetime import datetime
        datos = dict(BASE)
        datos["logs"] = [{"timestamp": datetime(2024, 9, 23), "severity": "ERROR",
                          "event": "e", "service": "s", "code": 500, "detail": "d"}]
        assert "2024-09-23" in _datos_del(ReportGenerator().render(datos))["logs"][0]["timestamp"]

    def test_el_bloque_no_deja_etiquetas_crudas(self):
        """Ni siquiera inertes: un < crudo dentro del bloque es ruido evitable."""
        datos = dict(BASE)
        datos["resolution"] = {**BASE["resolution"], "justification": "<script>alert(1)</script>"}
        html = ReportGenerator().render(datos)
        bloque = BLOQUE.search(html).group(1)
        assert "<" not in bloque and ">" not in bloque
        assert _datos_del(html)["resolution"]["justification"] == "<script>alert(1)</script>"
