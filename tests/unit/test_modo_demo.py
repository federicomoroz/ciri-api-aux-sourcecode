"""Modo demo: se ve el sistema funcionando sin que nadie pague la cuenta.

Las dos reglas que estos tests fijan:

1. En modo demo NO se llama al modelo. No es "intentar y fallar": no se gasta.
2. Un informe prearmado nunca se hace pasar por un análisis recién hecho —
   lo dicen el HTML, la cabecera de la respuesta y el log del servidor.
"""

import json
from types import SimpleNamespace

import pytest

from api.app.data.precomputados import (
    CARTEL,
    ETIQUETA,
    USO_DEMO,
    analisis_demo,
    casos_demo,
    informe_demo,
)
from api.app.routes.panel import (
    _emitir_demo,
    _en_modo_demo,
    _es_clave_invalida,
    _es_falta_de_saldo,
    _pagina_de_error,
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


def _peticion(txn=TXN_DEMO, api_key="", demo_mode=None):
    return SimpleNamespace(
        transaction_id=txn, api_key=api_key, motivo="No reconoce",
        cliente_vip=False, demo_mode=demo_mode,
    )


class TestQuienDecideElModo:
    def test_sin_indicacion_manda_la_configuracion_del_servidor(self, settings):
        assert _en_modo_demo(_peticion(), settings)
        settings.demo_mode = False
        assert not _en_modo_demo(_peticion(), settings)

    def test_el_toggle_del_panel_puede_encenderlo(self, settings):
        settings.demo_mode = False
        assert _en_modo_demo(_peticion(demo_mode=True), settings)

    def test_el_toggle_del_panel_puede_apagarlo(self, settings):
        assert not _en_modo_demo(_peticion(demo_mode=False), settings)

    def test_pedir_modo_demo_teniendo_clave_es_legitimo(self, settings):
        """Es justamente como se mira un caso sin gastar."""
        assert _en_modo_demo(_peticion(api_key=CLAVE_PROPIA, demo_mode=True), settings)

    def test_la_clave_sola_no_apaga_el_modo_demo(self, settings):
        """Tener clave no obliga a gastarla: para eso esta el toggle."""
        assert _en_modo_demo(_peticion(api_key=CLAVE_PROPIA), settings)


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

    def test_el_log_distingue_el_respaldo_por_falta_de_saldo(self, settings, caplog):
        """En modo demo no se llamo al modelo; en el respaldo se llamo y fallo."""
        import logging
        with caplog.at_level(logging.WARNING):
            _respuesta_demo(TXN_DEMO, settings, por_falta_de_saldo=True)
        assert "SIN SALDO" in caplog.text
        assert "MODO DEMO" not in caplog.text

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


class TestLosErroresSeNombran:
    """Quien evalua no tiene los logs: el error tiene que decir que hacer."""

    SIN_SALDO = Exception("Error 400: Your credit balance is too low to access the API")
    CLAVE_MALA = Exception("Error 401: {'type': 'authentication_error', 'message': 'invalid x-api-key'}")
    OTRO = Exception("Connection reset by peer")

    def test_distingue_falta_de_saldo(self):
        assert _es_falta_de_saldo(self.SIN_SALDO)
        assert not _es_falta_de_saldo(self.CLAVE_MALA)
        assert not _es_falta_de_saldo(self.OTRO)

    def test_distingue_clave_invalida(self):
        assert _es_clave_invalida(self.CLAVE_MALA)
        assert not _es_clave_invalida(self.SIN_SALDO)
        assert not _es_clave_invalida(self.OTRO)

    def test_sin_saldo_ofrece_el_modo_demo_como_salida(self, settings):
        assert "modo demo" in _pagina_de_error(SIN_INFORME, self.SIN_SALDO, settings)

    def test_clave_invalida_dice_como_es_una_clave_valida(self, settings):
        pagina = _pagina_de_error(SIN_INFORME, self.CLAVE_MALA, settings)
        assert "sk-ant-" in pagina
        assert "console.anthropic.com" in pagina

    def test_un_fallo_desconocido_no_promete_nada(self, settings):
        """Inventar una causa seria peor que decir que hay que mirar los logs."""
        pagina = _pagina_de_error(SIN_INFORME, self.OTRO, settings)
        assert "sk-ant-" not in pagina
        assert "saldo" not in pagina


class TestLaClaveDelVisitanteManda:
    """En modo produccion, la clave de la peticion reemplaza a la del servidor.

    Es lo que hace que evaluar el sistema a fondo no le cueste nada al dueño del
    repositorio: quien trae su clave gasta de su cuenta.
    """

    def test_el_pipeline_efimero_usa_la_clave_de_la_peticion(self, monkeypatch):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import api.app.routes.panel as panel

        claves_usadas = []

        def cliente_falso(api_key, model, tracer):
            claves_usadas.append(api_key)
            return MagicMock()

        monkeypatch.setattr(panel, "AnthropicClient", cliente_falso)
        monkeypatch.setattr(panel, "ResolutionService", lambda *a, **k: MagicMock())
        monkeypatch.setattr(panel, "PipelineService", lambda **k: MagicMock())

        base = SimpleNamespace(db=None, retriever=None, analyzer=None, report_gen=None)
        cfg = SimpleNamespace(llm_model="haiku", llm_model_resolution="sonnet")
        peticion = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tracer=MagicMock())))

        with panel._byok_pipeline(CLAVE_PROPIA, base, cfg, peticion):
            pass

        assert claves_usadas == [CLAVE_PROPIA, CLAVE_PROPIA]

    def test_los_clientes_se_cierran_al_terminar(self, monkeypatch):
        """Cada uno abre su pool de conexiones: sin cerrar, quedan colgados."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import api.app.routes.panel as panel

        creados = []

        def cliente_falso(api_key, model, tracer):
            c = MagicMock()
            creados.append(c)
            return c

        monkeypatch.setattr(panel, "AnthropicClient", cliente_falso)
        monkeypatch.setattr(panel, "ResolutionService", lambda *a, **k: MagicMock())
        monkeypatch.setattr(panel, "PipelineService", lambda **k: MagicMock())

        base = SimpleNamespace(db=None, retriever=None, analyzer=None, report_gen=None)
        cfg = SimpleNamespace(llm_model="haiku", llm_model_resolution="sonnet")
        peticion = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tracer=MagicMock())))

        with panel._byok_pipeline(CLAVE_PROPIA, base, cfg, peticion):
            pass

        assert all(c.close.called for c in creados)


class TestElAnalisisGuardado:
    """La resolucion y el juez pregrabados, que hacen correr el workflow de n8n.

    Sin esto, n8n se quedaba sin respuesta en el paso de sintesis. Con esto el
    flujo corre entero: las 7 consultas de contexto y el informe son reales, y
    lo unico pregrabado es lo que hubiera contestado el modelo.
    """

    ANALISIS = {
        "resolution": {"transaction_id": TXN_DEMO, "recommended_action": "REJECT",
                       "risk_level": "BLOCKER", "confidence": 0.95},
        "judge": {"overall_score": 8.6, "approved": True},
    }

    @pytest.fixture
    def settings(self, tmp_path):
        c = tmp_path / "informes"
        c.mkdir()
        (c / f"report_blocker_{TXN_DEMO}.html").write_text(CUERPO, encoding="utf-8")
        (c / f"analisis_{TXN_DEMO}.json").write_text(
            json.dumps(self.ANALISIS, ensure_ascii=False), encoding="utf-8"
        )
        return SimpleNamespace(demo_mode=True, demo_reports_path=str(c))

    def test_devuelve_la_resolucion_guardada(self, settings):
        d = analisis_demo(settings.demo_reports_path, TXN_DEMO)
        assert d["resolution"]["recommended_action"] == "REJECT"

    def test_devuelve_la_evaluacion_del_juez(self, settings):
        assert analisis_demo(settings.demo_reports_path, TXN_DEMO)["judge"]["overall_score"] == 8.6

    def test_un_caso_sin_analisis_no_inventa_nada(self, settings):
        assert analisis_demo(settings.demo_reports_path, SIN_INFORME) is None

    def test_es_indiferente_a_mayusculas(self, settings):
        assert analisis_demo(settings.demo_reports_path, TXN_DEMO.lower()) is not None

    def test_no_se_rompe_sin_transaccion(self, settings):
        assert analisis_demo(settings.demo_reports_path, "") is None

    def test_un_json_corrupto_no_tumba_la_api(self, settings, tmp_path):
        from pathlib import Path
        Path(settings.demo_reports_path, f"analisis_{SIN_INFORME}.json").write_text(
            "{roto", encoding="utf-8"
        )
        assert analisis_demo(settings.demo_reports_path, SIN_INFORME) is None


class TestLaPuertaDeResolveYJudge:
    ANALISIS = TestElAnalisisGuardado.ANALISIS

    @pytest.fixture
    def settings(self, tmp_path):
        c = tmp_path / "informes"
        c.mkdir()
        (c / f"analisis_{TXN_DEMO}.json").write_text(
            json.dumps(self.ANALISIS, ensure_ascii=False), encoding="utf-8"
        )
        return SimpleNamespace(demo_mode=True, demo_reports_path=str(c))

    def _demo(self, settings, txn, parte):
        from api.app.routes.analyze import _demo_de
        return _demo_de(settings, txn, parte)

    def test_en_modo_demo_devuelve_lo_guardado(self, settings):
        assert self._demo(settings, TXN_DEMO, "resolution")["recommended_action"] == "REJECT"

    def test_marca_la_respuesta_como_demo(self, settings):
        """La marca viaja hasta el informe, que la muestra como cartel."""
        assert self._demo(settings, TXN_DEMO, "resolution")["demo"] is True
        assert self._demo(settings, TXN_DEMO, "judge")["demo"] is True

    def test_con_el_modo_demo_apagado_no_intercepta(self, settings):
        """Si el modo esta apagado, tiene que llamar al modelo de verdad."""
        settings.demo_mode = False
        assert self._demo(settings, TXN_DEMO, "resolution") is None

    def test_un_caso_sin_guardar_pasa_al_modelo(self, settings):
        assert self._demo(settings, SIN_INFORME, "resolution") is None

    def test_una_parte_que_no_esta_pasa_al_modelo(self, settings, tmp_path):
        from pathlib import Path
        Path(settings.demo_reports_path, f"analisis_{OTRO_DEMO}.json").write_text(
            json.dumps({"resolution": {}}), encoding="utf-8"
        )
        assert self._demo(settings, OTRO_DEMO, "judge") is None

    def test_deja_warning_en_el_log(self, settings, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            self._demo(settings, TXN_DEMO, "resolution")
        assert "MODO DEMO" in caplog.text


class TestElInformeSeMarca:
    """Un informe armado con analisis guardado tiene que decirlo."""

    BASE = {
        "transaction": {"id": "TXN-1", "merchant": "x", "date": "2024-01-01", "amount_usd": 1.0,
                        "client_id": "c", "payment_method": "Cripto", "country": "COL",
                        "channel": "POS", "device": "d", "status": "s", "fraud_score": 8, "notes": ""},
        "judge_evaluation": {}, "agent_analysis": "", "merchant_risk": {}, "client_profile": {},
        "logs": [], "policies_evaluated": [], "similar_cases": [], "guardrail_warnings": [],
    }
    RESOLUCION = {"risk_level": "BLOCKER", "recommended_action": "REJECT",
                  "confidence": 0.9, "policy_verdicts": [], "next_steps": []}

    @staticmethod
    def _render(demo: bool) -> str:
        from api.app.reports.generator import ReportGenerator
        return ReportGenerator().render({
            **TestElInformeSeMarca.BASE,
            "resolution": {**TestElInformeSeMarca.RESOLUCION, "demo": demo},
        })

    def test_con_analisis_guardado_lleva_el_cartel(self):
        assert ETIQUETA in self._render(True)

    def test_un_analisis_real_no_lo_lleva(self):
        assert ETIQUETA not in self._render(False)

    def test_el_body_queda_marcado_para_estilos_y_scripts(self):
        assert 'data-demo="true"' in self._render(True)
        assert 'data-demo="true"' not in self._render(False)

    def test_el_texto_del_cartel_es_el_mismo_del_panel(self):
        """Una sola fuente: si cambia el texto, cambia en los dos lados."""
        assert CARTEL in self._render(True)
