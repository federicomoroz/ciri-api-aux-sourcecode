"""Tests del pipeline directo — el que usa el panel cuando no hay n8n.

Era la pieza mas compleja del proyecto y la unica sin tests fuera del E2E, que
se saltea si no hay API key. Por eso convivio tanto tiempo con un timeout que
no protegia de nada y con un cache que leia pero nunca escribia.
"""

import pytest

from api.app.domain.enums import PaymentMethod, ResolutionOutcome, RiskLevel, Severity
from api.app.domain.models import AnalyzeRequest
from api.app.services.pipeline import PipelineService

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

    def get_case_for_transaction(self, txn_id):
        return {"case_id": "CB-0001", "transaction_id": txn_id,
                "open_date": "2024-09-23", "close_date": "2024-10-11"}


class RetrieverFalso:
    def search_policies_and_cases(self, **kwargs):
        return ([{"code": "POL-EXC-003", "score": 0.9}], [{"case_id": "CB-0001", "score": 0.7}])


class AnalyzerFalso:
    def merchant_risk_profile(self, merchant):
        return {"merchant": merchant, "cb_ratio": 0.75, "flags": ["suspended_merchant"]}

    def client_flags(self, client_id):
        return {"client_id": client_id, "total_chargebacks": 0, "flags": []}

    def check_sla(self, case_open_date, country, cliente_vip=False, case_close_date=None):
        self.sla_pedido = {"open": case_open_date, "close": case_close_date}
        return {"within_sla": False, "days_elapsed": 12, "sla_limit_days": 10,
                "sla_type": "standard", "policy_reference": "POL-SLA-002",
                "compensation_applicable": True,
                "medido_desde": case_open_date, "medido_hasta": case_close_date,
                "caso_cerrado": case_close_date is not None}


class ResolucionFalsa:
    def __init__(self):
        self.ultimo_ctx = None

    def resolve(self, ctx):
        self.ultimo_ctx = ctx
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

    def test_el_sla_se_mide_sobre_el_reclamo_y_no_sobre_la_compra(self, pipeline):
        """Regresion: se medía desde la fecha de la transacción hasta hoy.

        Sobre un dataset de 2024 eso da el plazo vencido en el 100% de los casos
        y dispara la compensación de POL-SLA-004 siempre. El reloj de un reclamo
        corre mientras el reclamo está abierto.
        """
        pipeline.run(REQ)
        pedido = pipeline.analyzer.sla_pedido
        assert pedido["open"] == "2024-09-23", "usó la fecha de la compra, no la del reclamo"
        assert pedido["close"] == "2024-10-11", "no midió hasta el cierre del caso"

    def test_el_sla_llega_al_contexto(self, pipeline):
        """Regresion: el pipeline directo no consultaba el SLA y n8n si.

        Los dos caminos tienen que armar el mismo contexto, o el informe
        depende de por donde entro el caso.
        """
        pipeline.run(REQ)
        assert pipeline.resolution_svc.ultimo_ctx.sla["within_sla"] is False
        assert pipeline.resolution_svc.ultimo_ctx.sla["days_elapsed"] == 12

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
                         "sla", "resolving", "resolved", "judging", "judged"):
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
