"""Tests del pipeline directo — el que usa el panel cuando no hay n8n.

Era la pieza mas compleja del proyecto y la unica sin tests fuera del E2E, que
se saltea si no hay API key. Por eso convivio tanto tiempo con un timeout que
no protegia de nada y con un cache que leia pero nunca escribia.
"""

import pytest

from api.app.domain.models import AnalyzeRequest
from api.app.services.pipeline import PipelineService
from api.app.domain.enums import PaymentMethod, ResolutionOutcome, RiskLevel, Severity

TX = {
    "id": "TXN-00051", "client_id": "CLI-0003", "merchant": "Airbnb",
    "amount_usd": 100.0, "fraud_score": 8, "country": "COL",
    "payment_method": PaymentMethod.CRYPTO, "channel": "POS",
}


class DBFalsa:
    def __init__(self, cached=None):
        self.cache = {} if cached is None else dict(cached)
        self.escrituras = []

    def get_cached_report(self, key):
        return self.cache.get(key)

    def store_cached_report(self, key, html):
        self.escrituras.append(key)
        self.cache[key] = html

    def get_transaction(self, txn_id):
        return TX if txn_id == TX["id"] else None

    def get_logs_for_transaction(self, tx_id):
        return [{"severity": Severity.INFO, "event": "PAYMENT_INITIATED", "detail": "ok",
                 "timestamp": "2024-01-01", "code": "200"}]


class RetrieverFalso:
    def search_policies_and_cases(self, **kwargs):
        return ([{"code": "POL-EXC-003", "score": 0.9}], [{"case_id": "CB-0001", "score": 0.7}])


class AnalyzerFalso:
    def merchant_risk_profile(self, merchant):
        return {"merchant": merchant, "cb_ratio": 0.75, "flags": ["suspended_merchant"]}

    def client_flags(self, client_id):
        return {"client_id": client_id, "total_chargebacks": 0, "flags": []}


class ResolucionFalsa:
    def resolve(self, ctx):
        return {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER,
                "policy_verdicts": [], "guardrail_warnings": [],
                "_usage": {"input_tokens": 10, "output_tokens": 5, "call_count": 2}}

    def judge(self, **kwargs):
        return {"overall_score": 9.0, "approved": True,
                "_usage": {"input_tokens": 3, "output_tokens": 2, "call_count": 1}}


class ReporteFalso:
    def render(self, data):
        return f"<html>{data['transaction']['id']}</html>"


@pytest.fixture
def pipeline():
    return PipelineService(
        db=DBFalsa(), retriever=RetrieverFalso(), analyzer=AnalyzerFalso(),
        resolution_svc=ResolucionFalsa(), report_gen=ReporteFalso(),
    )


REQ = AnalyzeRequest(transaction_id="TXN-00051", motivo="No reconoce la compra")


class TestRun:
    def test_devuelve_html_y_uso(self, pipeline):
        html, usage = pipeline.run(REQ, model_name="claude-sonnet-4-6")
        assert "TXN-00051" in html
        assert usage["input_tokens"] == 13
        assert usage["output_tokens"] == 7
        assert usage["call_count"] == 3

    def test_guarda_el_informe_en_cache(self, pipeline):
        """Regresion: el pipeline leia el cache y nunca lo llenaba."""
        pipeline.run(REQ)
        assert pipeline.db.escrituras == ["TXN-00051|False"]

    def test_el_cache_evita_el_pipeline_entero(self):
        db = DBFalsa(cached={"TXN-00051|False": "<html>cacheado</html>"})
        p = PipelineService(db, RetrieverFalso(), AnalyzerFalso(), ResolucionFalsa(), ReporteFalso())
        html, usage = p.run(REQ)
        assert html == "<html>cacheado</html>"
        assert usage == {"cache_hit": True}

    def test_transaccion_inexistente(self, pipeline):
        with pytest.raises(ValueError, match="not found"):
            pipeline.run(AnalyzeRequest(transaction_id="TXN-99999", motivo="x"))

    def test_un_fallo_al_recolectar_contexto_se_propaga(self, pipeline):
        """No debe devolverse un informe armado con contexto incompleto."""
        def explota(_):
            raise RuntimeError("qdrant caido")

        pipeline.retriever.search_policies_and_cases = lambda **kw: explota(None)
        with pytest.raises(RuntimeError, match="qdrant caido"):
            pipeline.run(REQ)


class TestRunStreaming:
    def _eventos(self, pipeline, req=REQ):
        return list(pipeline.run_streaming(req))

    def test_emite_la_secuencia_completa(self, pipeline):
        pasos = [p for p, _ in self._eventos(pipeline)]
        assert pasos[0] == "start"
        assert pasos[-1] == "done"
        for esperado in ("cache_check", "transaction", "logs", "policies", "cases",
                         "resolving", "resolved", "judging", "judged"):
            assert esperado in pasos

    def test_tambien_cachea(self, pipeline):
        self._eventos(pipeline)
        assert pipeline.db.escrituras == ["TXN-00051|False"]

    def test_cache_hit_corta_en_el_primer_paso(self):
        db = DBFalsa(cached={"TXN-00051|False": "<html>cacheado</html>"})
        p = PipelineService(db, RetrieverFalso(), AnalyzerFalso(), ResolucionFalsa(), ReporteFalso())
        eventos = list(p.run_streaming(REQ))
        assert [paso for paso, _ in eventos] == ["start", "done"]
        assert eventos[-1][1]["html"] == "<html>cacheado</html>"

    def test_transaccion_inexistente_emite_error(self, pipeline):
        eventos = self._eventos(pipeline, AnalyzeRequest(transaction_id="TXN-99999", motivo="x"))
        assert eventos[-1][0] == "error"
