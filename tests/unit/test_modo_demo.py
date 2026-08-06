"""Modo demo: se ve el sistema funcionando sin que nadie pague la cuenta.

Las dos reglas que estos tests fijan:

1. En modo demo NO se llama al modelo. No es "intentar y fallar": no se gasta.
2. Un informe prearmado nunca se hace pasar por un análisis recién hecho —
   lo dicen el HTML, la cabecera de la respuesta y el log del servidor.
"""

import json
from types import SimpleNamespace

import pytest

from api.app.data.precomputados import CARTEL, ETIQUETA, USO_DEMO, casos_demo, informe_demo
from api.app.routes.panel import (
    _emitir_demo,
    _hay_que_ahorrar,
    _pagina_sin_caso_demo,
    _respuesta_demo,
)

TXN_DEMO = "TXN-00051"
OTRO_DEMO = "TXN-00042"
SIN_INFORME = "TXN-00099"
CLAVE_PROPIA = "sk-ant-la-del-evaluador"
CUERPO = "<html><head><title>x</title></head><body class='r'><h1>Caso</h1></body></html>"


@pytest.fixture
def settings(tmp_path):
    c = tmp_path / "informes"
    c.mkdir()
    (c / f"report_blocker_{TXN_DEMO}.html").write_text(CUERPO, encoding="utf-8")
    (c / f"report_high_{OTRO_DEMO}.html").write_text(CUERPO, encoding="utf-8")
    return SimpleNamespace(demo_mode=True, demo_reports_path=str(c), llm_model="haiku")


def _peticion(txn=TXN_DEMO, api_key=""):
    return SimpleNamespace(transaction_id=txn, api_key=api_key, motivo="No reconoce", cliente_vip=False)


class TestCuandoNoSeGasta:
    def test_en_modo_demo_sin_clave_propia_no_se_llama_al_modelo(self, settings):
        assert _hay_que_ahorrar(_peticion(), settings)

    def test_con_clave_propia_corre_el_pipeline_completo(self, settings):
        """Quien pone su clave paga lo suyo: el modo demo no le aplica."""
        assert not _hay_que_ahorrar(_peticion(api_key=CLAVE_PROPIA), settings)

    def test_con_el_modo_demo_apagado_corre_normal(self, settings):
        settings.demo_mode = False
        assert not _hay_que_ahorrar(_peticion(), settings)

    def test_el_modo_demo_apagado_manda_aunque_no_haya_clave(self, settings):
        settings.demo_mode = False
        assert not _hay_que_ahorrar(_peticion(api_key=""), settings)


class TestQueCasosCubre:
    def test_lista_los_casos_que_tienen_informe(self, settings):
        assert set(casos_demo(settings.demo_reports_path)) == {TXN_DEMO, OTRO_DEMO}

    def test_se_lee_de_la_carpeta_y_no_de_una_lista_escrita(self, settings):
        """Agregar un informe alcanza: no hay una segunda copia que actualizar."""
        from pathlib import Path
        Path(settings.demo_reports_path, f"report_low_{SIN_INFORME}.html").write_text(
            CUERPO, encoding="utf-8"
        )
        assert SIN_INFORME in casos_demo(settings.demo_reports_path)

    def test_ignora_lo_que_no_es_un_informe(self, settings):
        from pathlib import Path
        Path(settings.demo_reports_path, "notas.md").write_text("x", encoding="utf-8")
        assert len(casos_demo(settings.demo_reports_path)) == 2

    def test_carpeta_inexistente_no_rompe_nada(self):
        assert casos_demo("/carpeta/que/no/existe") == []
        assert informe_demo("/carpeta/que/no/existe", TXN_DEMO) is None


class TestLoDeclaraSiempre:
    @pytest.fixture
    def respuesta(self, settings):
        return _respuesta_demo(TXN_DEMO, settings)

    def test_el_html_abre_con_el_cartel(self, respuesta):
        html = respuesta.body.decode("utf-8")
        assert CARTEL in html
        assert html.index(CARTEL) < html.index("<h1>Caso</h1>")

    def test_el_cartel_lleva_la_etiqueta_pedida(self):
        assert ETIQUETA == "DEMO (Caso prearmado)"
        assert ETIQUETA in CARTEL

    def test_el_cartel_dice_que_no_se_genero_recien(self):
        assert "no se genero" in CARTEL

    def test_el_cartel_explica_como_correrlo_de_verdad(self):
        assert "API key" in CARTEL

    def test_la_cabecera_lo_marca_para_quien_consuma_la_api(self, respuesta):
        assert respuesta.headers["X-Modo-Demo"] == "true"

    def test_el_uso_informa_costo_cero_y_no_miente_con_la_cache(self, respuesta):
        uso = json.loads(respuesta.headers["X-Usage-JSON"])
        assert uso["demo"] is True
        assert uso["cost_usd"] == 0.0
        assert uso["cache_hit"] is False
        assert uso["call_count"] == 0

    def test_el_uso_trae_los_campos_que_el_panel_lee(self, respuesta):
        """Si falta uno, la vista de resultados del panel se rompe al renderizar."""
        uso = json.loads(respuesta.headers["X-Usage-JSON"])
        assert {"cost_usd", "total_tokens", "call_count", "model"} <= set(uso)

    def test_deja_un_warning_en_el_log(self, settings, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            _respuesta_demo(TXN_DEMO, settings)
        assert "MODO DEMO" in caplog.text
        assert TXN_DEMO in caplog.text

    def test_el_archivo_en_disco_queda_limpio(self, settings):
        """Los mismos HTML son ejemplos de la documentacion: ahi el cartel sobra."""
        from pathlib import Path
        crudo = Path(settings.demo_reports_path, f"report_blocker_{TXN_DEMO}.html")
        assert CARTEL not in crudo.read_text(encoding="utf-8")


class TestUnCasoSinInforme:
    def test_no_devuelve_el_informe_de_otro_caso(self, settings):
        assert _respuesta_demo(SIN_INFORME, settings) is None

    def test_la_pagina_explica_el_modo_demo(self, settings):
        assert "modo demo" in _pagina_sin_caso_demo(SIN_INFORME, settings)

    def test_la_pagina_ofrece_los_casos_que_si_andan(self, settings):
        pagina = _pagina_sin_caso_demo(SIN_INFORME, settings)
        assert TXN_DEMO in pagina and OTRO_DEMO in pagina

    def test_la_pagina_ofrece_la_salida_con_clave_propia(self, settings):
        assert "API key" in _pagina_sin_caso_demo(SIN_INFORME, settings)

    def test_aclara_que_lo_gratis_sigue_andando(self, settings):
        assert "SLA" in _pagina_sin_caso_demo(SIN_INFORME, settings)


class TestElStream:
    def _eventos(self, req, settings) -> list[dict]:
        return [json.loads(e.removeprefix("data: ").strip()) for e in _emitir_demo(req, settings)]

    def test_anuncia_el_modo_demo_en_el_primer_evento(self, settings):
        assert self._eventos(_peticion(), settings)[0] == {
            "step": "start", "transaction_id": TXN_DEMO, "demo": True,
        }

    def test_termina_entregando_el_informe(self, settings):
        final = self._eventos(_peticion(), settings)[-1]
        assert final["step"] == "done"
        assert CARTEL in final["html"]

    def test_el_uso_del_stream_tambien_informa_costo_cero(self, settings):
        assert self._eventos(_peticion(), settings)[-1]["usage"] == USO_DEMO

    def test_un_caso_sin_informe_termina_en_error_explicado(self, settings):
        final = self._eventos(_peticion(txn=SIN_INFORME), settings)[-1]
        assert final["step"] == "error"
        assert "API key" in final["message"]
        assert TXN_DEMO in final["message"]
