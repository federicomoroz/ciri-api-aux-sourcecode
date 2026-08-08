"""
Unit tests for the deterministic Analyzer module.
Uses in-memory SQLite — no Qdrant or LLM required.
"""

from datetime import date, timedelta

import pytest

from api.app.analysis.analyzer import Analyzer
from api.app.data.db import Database
from api.app.domain import decision, precedentes
from api.app.domain.enums import MerchantFlag, Severity


@pytest.fixture
def db(in_memory_db_path):
    return Database(in_memory_db_path)


@pytest.fixture
def analyzer(db):
    return Analyzer(db)


class TestSLACheck:

    def test_latam_standard_10_days(self, analyzer):
        """Standard LATAM SLA: 10 business days."""
        recent_date = (date.today() - timedelta(days=5)).isoformat()
        result = analyzer.check_sla(recent_date, "ARG", cliente_vip=False)
        assert result["sla_type"] == "standard"
        assert result["sla_limit_days"] == 10
        assert result["within_sla"] is True
        assert result["compensation_applicable"] is False

    def test_non_latam_extended_15_days(self, analyzer):
        """Non-LATAM extended SLA: 15 business days."""
        recent_date = (date.today() - timedelta(days=12)).isoformat()
        result = analyzer.check_sla(recent_date, "USA", cliente_vip=False)
        assert result["sla_type"] == "extended"
        assert result["sla_limit_days"] == 15
        assert result["within_sla"] is True  # 12 <= 15

    def test_vip_5_days(self, analyzer):
        """VIP client SLA: 5 business days."""
        recent_date = (date.today() - timedelta(days=3)).isoformat()
        result = analyzer.check_sla(recent_date, "MEX", cliente_vip=True)
        assert result["sla_type"] == "vip"
        assert result["sla_limit_days"] == 5
        assert result["within_sla"] is True

    def test_sla_breach_triggers_compensation(self, analyzer):
        """Exceeded SLA should flag compensation_applicable."""
        old_date = (date.today() - timedelta(days=20)).isoformat()
        result = analyzer.check_sla(old_date, "BRA", cliente_vip=False)
        assert result["within_sla"] is False
        assert result["compensation_applicable"] is True

    def test_vip_breach_before_standard(self, analyzer):
        """El SLA VIP es mas estricto: 6 habiles incumplen VIP pero no el estandar."""
        apertura = date(2024, 1, 1)          # lunes
        hoy = date(2024, 1, 9)               # martes siguiente: 6 dias habiles

        vip = analyzer.check_sla(apertura.isoformat(), "ARG", cliente_vip=True, today=hoy)
        estandar = analyzer.check_sla(apertura.isoformat(), "ARG", cliente_vip=False, today=hoy)

        assert vip["days_elapsed"] == 6
        assert vip["within_sla"] is False      # 6 > 5
        assert estandar["within_sla"] is True  # 6 <= 10

    def test_cuenta_habiles_no_corridos(self, analyzer):
        """Los limites de las politicas son en dias habiles."""
        apertura = date(2024, 1, 1)   # lunes
        hoy = date(2024, 1, 15)       # lunes, dos semanas despues

        r = analyzer.check_sla(apertura.isoformat(), "ARG", today=hoy)
        assert (hoy - apertura).days == 14   # corridos
        assert r["days_elapsed"] == 10       # habiles
        assert r["within_sla"] is True       # con corridos habria dado incumplimiento

    def test_fin_de_semana_no_suma(self, analyzer):
        apertura = date(2024, 1, 5)   # viernes
        hoy = date(2024, 1, 8)        # lunes
        r = analyzer.check_sla(apertura.isoformat(), "ARG", today=hoy)
        assert r["days_elapsed"] == 1


class TestErrorPatterns:

    def test_systematic_merchant_timeout(self, analyzer):
        """MERCHANT_NO_RESPONSE x2 should detect systematic_merchant_timeout."""
        logs = [
            {"severity": Severity.ERROR, "event": "MERCHANT_NO_RESPONSE", "detail": "timeout", "timestamp": "2024-01-01 10:00:00", "code": "408"},
            {"severity": Severity.ERROR, "event": "MERCHANT_NO_RESPONSE", "detail": "timeout again", "timestamp": "2024-01-01 10:01:00", "code": "408"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "systematic_merchant_timeout" in result["patterns"]

    def test_no_timeout_pattern_with_single_occurrence(self, analyzer):
        """Single MERCHANT_NO_RESPONSE should NOT trigger systematic pattern."""
        logs = [
            {"severity": Severity.WARN, "event": "MERCHANT_NO_RESPONSE", "detail": "once", "timestamp": "2024-01-01 10:00:00", "code": "408"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "systematic_merchant_timeout" not in result["patterns"]

    def test_fraud_block_pattern(self, analyzer):
        """FRAUD_ALERT + AUTH_DECLINED should detect blocked_for_fraud."""
        logs = [
            {"severity": Severity.WARN, "event": "FRAUD_ALERT", "detail": "score low", "timestamp": "2024-01-01", "code": "200"},
            {"severity": Severity.ERROR, "event": "AUTH_DECLINED", "detail": "blocked", "timestamp": "2024-01-01", "code": "402"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "blocked_for_fraud" in result["patterns"]

    def test_severity_counts(self, analyzer):
        """Severity counts should be accurate."""
        logs = [
            {"severity": Severity.ERROR, "event": "WEBHOOK_FAILED", "detail": "", "timestamp": "", "code": "500"},
            {"severity": Severity.ERROR, "event": "WEBHOOK_FAILED", "detail": "", "timestamp": "", "code": "500"},
            {"severity": Severity.WARN, "event": "TIMEOUT_RETRY", "detail": "", "timestamp": "", "code": "408"},
            {"severity": Severity.INFO, "event": "AUTH_REQUEST", "detail": "", "timestamp": "", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert result["severity_counts"][Severity.ERROR] == 2
        assert result["severity_counts"][Severity.WARN] == 1
        assert result["severity_counts"][Severity.INFO] == 1

    def test_empty_logs(self, analyzer):
        """Empty logs should return empty result."""
        result = analyzer.detect_error_patterns([])
        assert result["patterns"] == []
        assert result["severity_counts"] == {Severity.ERROR: 0, Severity.WARN: 0, Severity.INFO: 0}

    def test_duplicate_charge_pattern(self, analyzer):
        """DOUBLE_CHARGE_DETECT should detect duplicate_charge."""
        logs = [
            {"severity": Severity.ERROR, "event": "DOUBLE_CHARGE_DETECT", "detail": "duplicate", "timestamp": "2024-01-01", "code": "409"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "duplicate_charge" in result["patterns"]

    def test_sla_violation_pattern(self, analyzer):
        """SLA_BREACH should detect sla_violation."""
        logs = [
            {"severity": Severity.WARN, "event": "SLA_BREACH", "detail": "SLA exceeded", "timestamp": "2024-01-01", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "sla_violation" in result["patterns"]

    def test_integration_failure_pattern(self, analyzer):
        """WEBHOOK_FAILED should detect integration_failure."""
        logs = [
            {"severity": Severity.ERROR, "event": "WEBHOOK_FAILED", "detail": "500 error", "timestamp": "2024-01-01", "code": "500"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "integration_failure" in result["patterns"]

    def test_session_interrupted_payment_pattern(self, analyzer):
        """SESSION_EXPIRED + PAYMENT_INITIATED should detect session_interrupted_payment."""
        logs = [
            {"severity": Severity.INFO, "event": "PAYMENT_INITIATED", "detail": "starting", "timestamp": "2024-01-01 10:00:00", "code": "200"},
            {"severity": Severity.WARN, "event": "SESSION_EXPIRED", "detail": "session timeout", "timestamp": "2024-01-01 10:05:00", "code": "401"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "session_interrupted_payment" in result["patterns"]

    def test_geographic_anomaly_pattern(self, analyzer):
        """GEO_ANOMALY should detect geographic_anomaly."""
        logs = [
            {"severity": Severity.WARN, "event": "GEO_ANOMALY", "detail": "unusual location", "timestamp": "2024-01-01", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "geographic_anomaly" in result["patterns"]

    def test_connectivity_issue_pattern(self, analyzer):
        """TIMEOUT_RETRY should detect connectivity_issue."""
        logs = [
            {"severity": Severity.WARN, "event": "TIMEOUT_RETRY", "detail": "retrying", "timestamp": "2024-01-01", "code": "408"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "connectivity_issue" in result["patterns"]

    def test_multiple_patterns_detected(self, analyzer):
        """Multiple patterns in same log set should all be detected."""
        logs = [
            {"severity": Severity.WARN, "event": "FRAUD_ALERT", "detail": "score low", "timestamp": "2024-01-01", "code": "200"},
            {"severity": Severity.ERROR, "event": "AUTH_DECLINED", "detail": "blocked", "timestamp": "2024-01-01", "code": "402"},
            {"severity": Severity.ERROR, "event": "DOUBLE_CHARGE_DETECT", "detail": "duplicate", "timestamp": "2024-01-01", "code": "409"},
            {"severity": Severity.WARN, "event": "GEO_ANOMALY", "detail": "location", "timestamp": "2024-01-01", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "blocked_for_fraud" in result["patterns"]
        assert "duplicate_charge" in result["patterns"]
        assert "geographic_anomaly" in result["patterns"]


class TestMerchantRisk:

    def test_merchant_risk_returns_dict(self, analyzer):
        """merchant_risk_profile should return a dict with required keys."""
        result = analyzer.merchant_risk_profile("Airbnb")
        assert "merchant" in result
        assert "cb_ratio" in result
        assert "total_transactions" in result
        assert "flags" in result
        assert "is_strategic" in result

    def test_unknown_merchant_returns_zeros(self, analyzer):
        """Unknown merchant should return zero counts."""
        result = analyzer.merchant_risk_profile("NonExistentMerchant999")
        assert result["total_transactions"] == 0
        assert result["cb_ratio"] == 0.0


class TestClientFlags:

    def test_client_history_returns_dict(self, analyzer):
        """client_flags should return a dict with required keys."""
        result = analyzer.client_flags("CLI-0003")
        assert "client_id" in result
        assert "total_transactions" in result
        assert "total_chargebacks" in result
        assert "flags" in result

    def test_unknown_client_returns_empty(self, analyzer):
        """Unknown client should return zero counts."""
        result = analyzer.client_flags("CLI-9999")
        assert result["total_transactions"] == 0


class TestPatronesEnElResumenDeLogs:
    """Los patrones detectados tienen que llegar al prompt, no quedarse en la funcion.

    Regresion: detect_error_patterns existia y estaba probado, pero ningun camino
    de la aplicacion lo llamaba. El modelo nunca veia los patrones.
    """

    LOGS = [
        {"severity": Severity.ERROR, "event": "MERCHANT_NO_RESPONSE", "detail": "timeout",
         "timestamp": "2024-01-01", "code": "504"},
        {"severity": Severity.ERROR, "event": "MERCHANT_NO_RESPONSE", "detail": "timeout",
         "timestamp": "2024-01-01", "code": "504"},
        {"severity": Severity.ERROR, "event": "MERCHANT_NO_RESPONSE", "detail": "timeout",
         "timestamp": "2024-01-01", "code": "504"},
        {"severity": Severity.WARN, "event": "DOUBLE_CHARGE_DETECT", "detail": "doble cobro",
         "timestamp": "2024-01-01", "code": "200"},
    ]

    def test_el_resumen_incluye_los_patrones(self):

        resumen = precedentes.resumir_logs(self.LOGS)
        assert "Patrones detectados" in resumen
        assert "systematic_merchant_timeout" in resumen
        assert "duplicate_charge" in resumen

    def test_sin_patrones_no_agrega_la_linea(self):

        limpios = [{"severity": Severity.INFO, "event": "PAYMENT_INITIATED", "detail": "ok",
                    "timestamp": "2024-01-01", "code": "200"}]
        assert "Patrones detectados" not in precedentes.resumir_logs(limpios)

    def test_sin_logs_no_rompe(self):

        assert "Total: 0 eventos" in precedentes.resumir_logs([])


class TestLineaBaseDelCorpus:
    """Un ratio de contracargos solo significa algo contra una referencia.

    Regresion: el umbral era 0.02 —el de la industria sobre el libro de ventas
    completo de un comercio— aplicado a un dataset que es una muestra de
    disputas, donde casi la mitad de las transacciones terminaron en contracargo.
    Los quince comercios salian suspendidos: un flag que da positivo siempre no
    distingue nada, y arrastraba cada caso a riesgo HIGH.
    """

    @staticmethod
    def _analyzer(baseline: float, cb_ratio: float):
        from unittest.mock import MagicMock

        db = MagicMock()
        db.get_corpus_cb_ratio.return_value = baseline
        db.get_merchant_stats.return_value = {
            "merchant": "X", "total_transactions": 10, "total_chargebacks": 5,
            "cb_ratio": cb_ratio, "total_volume_usd": 1000.0, "avg_transaction_usd": 100.0,
        }
        return Analyzer(db)

    def test_muy_por_encima_de_la_base_se_suspende(self):
        perfil = self._analyzer(0.47, 1.11).merchant_risk_profile("AliExpress")
        assert MerchantFlag.SUSPENDED_MERCHANT in perfil["flags"]

    def test_apenas_por_encima_es_ratio_alto_no_suspension(self):
        perfil = self._analyzer(0.47, 0.60).merchant_risk_profile("Steam")
        assert MerchantFlag.HIGH_CB_RATIO in perfil["flags"]
        assert MerchantFlag.SUSPENDED_MERCHANT not in perfil["flags"]

    def test_en_la_base_no_se_marca(self):
        perfil = self._analyzer(0.47, 0.47).merchant_risk_profile("eBay")
        assert perfil["flags"] == []

    def test_muy_por_debajo_no_se_marca(self):
        perfil = self._analyzer(0.47, 0.11).merchant_risk_profile("Spotify")
        assert perfil["flags"] == []

    def test_el_perfil_devuelve_su_referencia(self):
        """0.75 no dice nada; 0.75 contra una base de 0.47, si."""
        perfil = self._analyzer(0.47, 0.75).merchant_risk_profile("Airbnb")
        assert perfil["cb_ratio_baseline"] == 0.47

    def test_sobre_un_libro_de_ventas_real_reproduce_el_umbral_clasico(self):
        """Con una base del 1%, 1.5x da 1.5% — el orden de magnitud de la industria."""
        assert MerchantFlag.SUSPENDED_MERCHANT in (
            self._analyzer(0.01, 0.02).merchant_risk_profile("X")["flags"]
        )
        assert self._analyzer(0.01, 0.005).merchant_risk_profile("X")["flags"] == []


class TestSLASobreElReclamo:
    """El reloj corre mientras el reclamo esta abierto, no hasta hoy.

    Regresion: se medía hasta `now()`. Sobre un dataset fechado en 2024, eso da
    el plazo vencido en el 100% de los casos y dispara la compensacion de
    POL-SLA-004 siempre.
    """

    @staticmethod
    def _analyzer(sla_dias: int | None = None):
        from unittest.mock import MagicMock

        db = MagicMock()
        db.get_policy.return_value = {"sla_dias": sla_dias} if sla_dias else {}
        return Analyzer(db)

    def test_un_caso_cerrado_a_tiempo_cumple_aunque_sea_viejo(self):
        r = self._analyzer().check_sla(
            "2024-09-23", "COL", case_close_date="2024-10-01", today=date(2026, 8, 7),
        )
        assert r["within_sla"] is True
        assert r["compensation_applicable"] is False
        assert r["caso_cerrado"] is True

    def test_un_caso_cerrado_tarde_no_cumple(self):
        r = self._analyzer().check_sla(
            "2024-09-23", "COL", case_close_date="2024-11-15", today=date(2026, 8, 7),
        )
        assert r["within_sla"] is False
        assert r["compensation_applicable"] is True

    def test_un_caso_abierto_se_mide_hasta_hoy(self):
        r = self._analyzer().check_sla("2026-08-03", "COL", today=date(2026, 8, 7))
        assert r["caso_cerrado"] is False
        assert r["medido_hasta"] == "2026-08-07"
        assert r["within_sla"] is True

    def test_el_plazo_sale_de_la_politica_no_de_una_constante(self):
        """Editar POL-SLA-002 por la API cambia el plazo sin deploy."""
        r = self._analyzer(sla_dias=3).check_sla(
            "2024-09-23", "COL", case_close_date="2024-10-01", today=date(2026, 8, 7),
        )
        assert r["sla_limit_days"] == 3
        assert r["within_sla"] is False, "6 dias habiles superan un plazo de 3"

    def test_sin_plazo_en_la_politica_usa_el_de_respaldo(self):
        r = self._analyzer().check_sla("2024-09-23", "COL", case_close_date="2024-10-01")
        assert r["sla_limit_days"] == 10

    def test_devuelve_contra_que_conto(self):
        r = self._analyzer().check_sla(
            "2024-09-23", "COL", case_close_date="2024-10-01", today=date(2026, 8, 7),
        )
        assert r["medido_desde"] == "2024-09-23"
        assert r["medido_hasta"] == "2024-10-01"


class TestSinReclamoRegistradoNoSeMideElPlazo:
    """El SLA se contaba desde la fecha de COMPRA cuando no habia caso abierto.

    El arreglo anterior cubrio los casos cerrados —se miden hasta su cierre, no
    hasta hoy— y dejo afuera los que no tienen reclamo registrado: 53 de las 100
    transacciones del dataset, incluida TXN-00051, la del escenario estrella.

    Ahi la apertura caia a la fecha de la transaccion y el corte era hoy:
    TXN-00051 daba 489 dias habiles contra un limite de 10, y el informe
    afirmaba una compensacion de USD 15 al lado del veredicto de la misma
    politica diciendo que el caso recien empieza. Se contradecia en la misma
    pagina.

    Entre la compra y el reclamo pueden pasar meses. Sin reclamo no hay reloj.
    """

    @staticmethod
    def _analyzer(tmp_path):
        import sqlite3

        from api.app.analysis.analyzer import Analyzer
        from api.app.data.db import Database

        ruta = str(tmp_path / "a.db")
        c = sqlite3.connect(ruta)
        c.executescript(
            "CREATE TABLE transactions (id TEXT, merchant TEXT, amount_usd REAL, date TEXT);"
            "CREATE TABLE cases (case_id TEXT, transaction_id TEXT, open_date TEXT, close_date TEXT);"
            "CREATE TABLE policies (code TEXT, sla_dias INT, puede_bloquear INT);"
        )
        c.commit()
        c.close()
        return Analyzer(Database(ruta))

    def test_sin_apertura_no_afirma_incumplimiento(self, tmp_path):
        r = self._analyzer(tmp_path).check_sla(case_open_date="", country="COL")
        assert r["within_sla"] is None, "afirmo un plazo que nadie midio"
        assert r["days_elapsed"] is None

    def test_sin_apertura_no_paga_compensacion(self, tmp_path):
        """Se paga cuando consta que se incumplio, no cuando no consta nada."""
        r = self._analyzer(tmp_path).check_sla(case_open_date="", country="COL")
        assert r["compensation_applicable"] is False

    def test_lo_declara_para_que_el_informe_lo_pueda_decir(self, tmp_path):
        r = self._analyzer(tmp_path).check_sla(case_open_date="", country="COL")
        assert r["sin_reclamo_registrado"] is True

    def test_el_plazo_que_concede_la_politica_se_informa_igual(self, tmp_path):
        """Es un dato del caso, no depende de que haya reclamo."""
        r = self._analyzer(tmp_path).check_sla(case_open_date="", country="COL")
        assert r["sla_limit_days"] > 0
        assert r["policy_reference"]

    def test_con_reclamo_abierto_si_se_mide(self, tmp_path):
        r = self._analyzer(tmp_path).check_sla(case_open_date="2024-01-01", country="COL")
        assert r["within_sla"] is False
        assert r["compensation_applicable"] is True

    def test_un_reclamo_de_ayer_esta_en_plazo(self, tmp_path):
        from datetime import UTC, datetime, timedelta

        ayer = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        r = self._analyzer(tmp_path).check_sla(case_open_date=ayer, country="COL")
        assert r["within_sla"] is True
        assert r["compensation_applicable"] is False


class TestLaCompensacionNoSaleDeUnPlazoQueNoSeMidio:
    """`not sla.get("within_sla", True)` daba True con None.

    O sea que el arreglo de arriba se filtraba igual por el otro camino: el
    plazo no se medía, pero la compensacion salia lo mismo.
    """

    TX = {"amount_usd": 2095.90}

    @staticmethod
    def _comp(sla):
        return decision.compensacion_por_sla(
            sla, TestLaCompensacionNoSaleDeUnPlazoQueNoSeMidio.TX)

    def test_un_plazo_sin_medir_no_compensa(self):
        assert self._comp({"within_sla": None})["compensation_applicable"] is False

    def test_un_plazo_incumplido_si(self):
        assert self._comp({"within_sla": False})["compensation_applicable"] is True

    def test_un_plazo_cumplido_no(self):
        assert self._comp({"within_sla": True})["compensation_applicable"] is False

    def test_sin_dato_de_sla_no_determina_nada(self):
        """Ahi decide el modelo, sujeto a los guardrails."""
        assert self._comp({}) == {}
