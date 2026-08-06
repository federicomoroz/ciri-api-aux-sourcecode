"""Tests de las piezas compartidas que salieron de la auditoria.

Cada una reemplazo codigo que estaba escrito dos o mas veces en lugares
distintos. Los tests fijan que sigan siendo una sola.
"""

from dataclasses import FrozenInstanceError

import pytest

from api.app.domain.context import CaseContext
from api.app.domain.models import ResolveRequest
from api.app.llm.pricing import estimar_costo_usd, tarifa_de
from api.app.llm.prompts._shared import bloque_json
from api.app.rag.formatter import envolver_resultados


class TestTarifas:
    """El costo se calculaba en dos servicios y ya habian divergido."""

    def test_reconoce_el_modelo_pese_a_los_sufijos(self):
        assert tarifa_de("claude-sonnet-4-6") == tarifa_de("claude-sonnet-4-6-20250101")

    def test_sonnet_cuesta_mas_que_haiku(self):
        assert estimar_costo_usd("claude-sonnet-4-6", 1_000_000, 0) > estimar_costo_usd(
            "claude-haiku-4-5", 1_000_000, 0
        )

    def test_modelo_desconocido_estima_en_vez_de_informar_cero(self):
        """Informar cero costo seria peor que estimar con la tarifa de referencia."""
        assert estimar_costo_usd("modelo-inexistente", 1_000_000, 1_000_000) > 0

    def test_sin_tokens_no_hay_costo(self):
        assert estimar_costo_usd("claude-sonnet-4-6", 0, 0) == 0

    def test_es_lineal_en_los_tokens(self):
        uno = estimar_costo_usd("claude-sonnet-4-6", 1000, 500)
        dos = estimar_costo_usd("claude-sonnet-4-6", 2000, 1000)
        assert dos == pytest.approx(uno * 2)

    def test_no_se_rompe_sin_nombre_de_modelo(self):
        assert estimar_costo_usd("", 100, 100) > 0


class TestBloqueJson:
    def test_conserva_los_acentos(self):
        """Sin ensure_ascii=False el modelo recibe escapes en vez de texto."""
        assert "Créditos" in bloque_json({"tipo": "Créditos"})
        assert "\\u" not in bloque_json({"tipo": "Créditos"})

    def test_sangra_para_que_se_lea_la_estructura(self):
        assert "\n  " in bloque_json({"a": 1, "b": 2})


class TestEnvolverResultados:
    def test_devuelve_la_consulta_que_realmente_se_ejecuto(self):
        resultados = [{"code": "POL-001", "_query": "consulta enriquecida"}]
        assert envolver_resultados(resultados, "texto")["query_used"] == "consulta enriquecida"

    def test_sin_resultados_devuelve_la_consulta_original(self):
        assert envolver_resultados([], "", "lo que pidio el usuario")["query_used"] == "lo que pidio el usuario"

    def test_cuenta_los_resultados(self):
        assert envolver_resultados([{}, {}, {}], "")["count"] == 3

    def test_incluye_el_texto_para_el_modelo(self):
        assert envolver_resultados([], "### Politica 1")["formatted_for_llm"] == "### Politica 1"


class TestCaseContext:
    """Un solo tipo para lo que antes eran tres, con tres juegos de nombres."""

    def test_la_peticion_de_n8n_se_traduce_sin_perder_nada(self):
        req = ResolveRequest(
            transaction_id="TXN-00051",
            tx_data={"id": "TXN-00051", "merchant": "Airbnb"},
            policies=[{"code": "POL-EXC-003"}],
            similar_cases=[{"case_id": "CB-0001"}],
            logs=[{"event": "AUTH_DECLINED"}],
            merchant_risk={"cb_ratio": 0.75},
            client_history={"total_chargebacks": 2},
            motivo="No reconoce la compra",
            cliente_vip=True,
        )
        ctx = req.to_context()
        assert ctx.transaction["merchant"] == "Airbnb"
        assert ctx.transaction_id == "TXN-00051"
        assert ctx.policies[0]["code"] == "POL-EXC-003"
        assert ctx.similar_cases[0]["case_id"] == "CB-0001"
        assert ctx.logs[0]["event"] == "AUTH_DECLINED"
        assert ctx.merchant_risk["cb_ratio"] == 0.75
        assert ctx.client_history["total_chargebacks"] == 2
        assert ctx.motivo == "No reconoce la compra"
        assert ctx.cliente_vip is True

    def test_transaccion_sin_id(self):
        assert CaseContext(transaction={}).transaction_id == ""

    def test_las_colecciones_arrancan_vacias_y_no_se_comparten(self):
        a, b = CaseContext(transaction={}), CaseContext(transaction={})
        a.logs.append({"event": "x"})
        assert b.logs == []

    def test_la_evidencia_del_juez_lleva_los_nombres_de_su_prompt(self):
        ctx = CaseContext(transaction={"id": "TXN-1"}, motivo="fraude", logs=[{"a": 1}])
        evidencia = ctx.para_el_juez()
        assert set(evidencia) == {
            "transaction", "motivo", "policies", "similar_cases",
            "merchant_risk", "client_history", "logs",
        }
        assert evidencia["transaction"]["id"] == "TXN-1"

    def test_es_inmutable(self):
        """El contexto se arma una vez y viaja; nadie deberia reescribirlo."""
        ctx = CaseContext(transaction={"id": "TXN-1"})
        with pytest.raises(FrozenInstanceError):
            ctx.transaction = {}


class TestClasificarResolucion:
    """Las mismas palabras deciden la nota al modelo y el conteo de la tendencia.

    Estaban escritas en tres lugares del mismo metodo: la tabla de implicaciones,
    el conteo de aprobados/rechazados y el conteo sobre los que matchean motivo.
    """

    @staticmethod
    def _clasificar(texto):
        from api.app.services.resolution import ResolutionService

        return ResolutionService._clasificar_resolucion(texto)

    def test_reconoce_las_cinco_clases(self):
        casos = {
            "Reembolso aprobado": "aprobado",
            "Resuelto a favor del cliente": "aprobado",
            "Contracargo rechazado": "rechazado",
            "Denegado por falta de pruebas": "rechazado",
            "Reembolso parcial": "parcial",
            "Sin resolucion": "sin_resolver",
            "Pendiente de revision": "sin_resolver",
            "Cerrado": "cerrado",
        }
        for texto, esperada in casos.items():
            assert self._clasificar(texto)[0] == esperada, texto

    def test_toda_clase_conocida_trae_su_implicacion(self):
        clase, implicacion = self._clasificar("Reembolso aprobado")
        assert clase == "aprobado"
        assert "favorable al cliente" in implicacion

    def test_resolucion_desconocida_no_inventa_clase(self):
        assert self._clasificar("Escalado a legales") == (None, None)

    def test_no_se_rompe_con_vacio(self):
        assert self._clasificar("") == (None, None)
        assert self._clasificar(None) == (None, None)

    def test_es_insensible_a_mayusculas(self):
        assert self._clasificar("APROBADO")[0] == "aprobado"

    def test_gana_la_primera_coincidencia(self):
        """El orden de la tabla define la precedencia cuando hay ambiguedad."""
        assert self._clasificar("Pendiente, luego aprobado")[0] == "sin_resolver"
