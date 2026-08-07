"""Modo demo: se ve el sistema funcionando sin que nadie pague la cuenta.

Las dos reglas que estos tests fijan:

1. En modo demo NO se llama al modelo. No es "intentar y fallar": no se gasta.
2. Un informe prearmado nunca se hace pasar por un análisis recién hecho —
   lo dicen el HTML, la cabecera de la respuesta y el log del servidor.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.app.data import precomputados
from api.app.data.precomputados import (
    CARTEL,
    ETIQUETA,
    USO_DEMO,
    analisis_demo,
    caso_mas_cercano_en_riesgo,
    casos_demo,
    distancia_de_riesgo,
    informe_de_ejemplo,
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
    """Antes devolvia un error. Ahora devuelve el ejemplo mas cercano en riesgo.

    Cambio a proposito: quien prueba un caso cualquiera tiene que recibir algo
    que mire, no una pagina de error — siempre que quede dicho de que caso es.
    """

    def test_responde_con_un_ejemplo_en_vez_de_nada(self, settings):
        assert _respuesta_demo(SIN_INFORME, settings) is not None

    def test_el_ejemplo_declara_que_no_es_el_caso_pedido(self, settings):
        html = _respuesta_demo(SIN_INFORME, settings).body.decode("utf-8")
        assert SIN_INFORME in html
        assert "no tiene un analisis guardado" in html

    def test_sin_ningun_informe_guardado_si_explica_en_vez_de_inventar(self, tmp_path):
        vacia = tmp_path / "sin_nada"
        vacia.mkdir()
        cfg = SimpleNamespace(demo_mode=True, demo_reports_path=str(vacia))
        assert _respuesta_demo(SIN_INFORME, cfg) is None

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

    def test_un_caso_sin_informe_termina_igual_con_un_ejemplo(self, settings):
        final = self._eventos(_peticion(txn=SIN_INFORME), settings)[-1]
        assert final["step"] == "done"
        assert SIN_INFORME in final["html"]
        assert "no tiene un analisis guardado" in final["html"]

    def test_sin_ningun_informe_guardado_el_stream_explica(self, tmp_path):
        vacia = tmp_path / "sin_nada"
        vacia.mkdir()
        cfg = SimpleNamespace(demo_mode=True, demo_reports_path=str(vacia))
        final = self._eventos(_peticion(txn=SIN_INFORME), cfg)[-1]
        assert final["step"] == "error"
        assert "API key" in final["message"]


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
    """Lo que viaja por peticion es de quien la hace, y no se guarda.

    Dos cosas: la clave —para que evaluar el sistema a fondo no le cueste al
    dueño del repositorio— y la eleccion de modelo, para que un visitante pueda
    probar otro sin cambiarselo a nadie mas.
    """

    @staticmethod
    def _panel_falso(monkeypatch, registro):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        import api.app.routes.panel as panel
        from api.app.domain.constants import PASOS_DEL_PIPELINE

        modelos = MagicMock()

        def clientes_para(override, api_key=""):
            registro["override"] = override
            registro["api_key"] = api_key
            registro["clientes"] = {p: MagicMock() for p in PASOS_DEL_PIPELINE}
            return registro["clientes"]

        modelos.clientes_para.side_effect = clientes_para
        modelos.clientes_demo.return_value = None
        monkeypatch.setattr(panel, "ResolutionService", lambda *a, **k: MagicMock())
        monkeypatch.setattr(panel, "PipelineService", lambda **k: MagicMock())

        base = SimpleNamespace(db=None, retriever=None, analyzer=None, report_gen=None)
        peticion = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            tracer=MagicMock(), modelos_service=modelos,
        )))
        return panel, base, peticion

    def test_la_clave_de_la_peticion_llega_a_los_clientes(self, monkeypatch):
        registro = {}
        panel, base, peticion = self._panel_falso(monkeypatch, registro)

        with panel._pipeline_efimero(base, peticion, api_key=CLAVE_PROPIA):
            pass

        assert registro["api_key"] == CLAVE_PROPIA

    def test_la_eleccion_de_modelo_de_la_sesion_tambien_viaja(self, monkeypatch):
        registro = {}
        panel, base, peticion = self._panel_falso(monkeypatch, registro)
        eleccion = {"judge": {"proveedor": "groq", "modelo": "llama-3.3-70b-versatile"}}

        with panel._pipeline_efimero(base, peticion, override=eleccion):
            pass

        assert registro["override"] == eleccion

    def test_se_construye_un_cliente_por_cada_paso(self, monkeypatch):
        from api.app.domain.constants import PASOS_DEL_PIPELINE

        registro = {}
        panel, base, peticion = self._panel_falso(monkeypatch, registro)

        with panel._pipeline_efimero(base, peticion, api_key=CLAVE_PROPIA):
            pass

        assert set(registro["clientes"]) == set(PASOS_DEL_PIPELINE)

    def test_al_terminar_le_pide_al_manager_que_cierre(self, monkeypatch):
        """Cada cliente abre su pool: sin cerrar, quedan colgados.

        Cerrarlos es del manager y no de acá: él sabe cuáles son de esta
        petición y cuáles comparte con las demás. Cerrar a mano cerraba también
        los compartidos, y la petición siguiente moría con «Cannot send a
        request, as the client has been closed» — ver `test_modelos.py`.
        """
        registro = {}
        panel, base, peticion = self._panel_falso(monkeypatch, registro)
        manager = peticion.app.state.modelos_service.manager

        with panel._pipeline_efimero(base, peticion, api_key=CLAVE_PROPIA):
            pass

        manager.cerrar_todos.assert_called_once_with(registro["clientes"])


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


class TestElEjemploMasCercanoEnRiesgo:
    """Un caso sin análisis guardado recibe el más parecido en riesgo, declarado.

    La comparación es por score antifraude y nada más: es la única medida de
    riesgo disponible sin correr el pipeline, y es la que decide POL-FRD-001.
    """

    CASOS = {
        "TXN-00051": {"fraud_score": 8, "risk_level": "BLOCKER"},
        "TXN-00042": {"fraud_score": 4, "risk_level": "HIGH"},
        "TXN-00089": {"fraud_score": 80, "risk_level": "HIGH"},
    }

    @pytest.fixture
    def carpeta(self, tmp_path):
        c = tmp_path / "informes"
        c.mkdir()
        for txn, caso in self.CASOS.items():
            (c / f"report_x_{txn}.html").write_text(
                f"<html><body><h1>{txn}</h1></body></html>", encoding="utf-8"
            )
            (c / f"analisis_{txn}.json").write_text(
                json.dumps({"resolution": {"transaction_id": txn}, "judge": {},
                            "caso": {**caso, "transaction_id": txn}}),
                encoding="utf-8",
            )
        return str(c)

    def test_elige_el_de_score_mas_cercano(self, carpeta):
        assert caso_mas_cercano_en_riesgo(carpeta, {"fraud_score": 7}) == "TXN-00051"
        assert caso_mas_cercano_en_riesgo(carpeta, {"fraud_score": 3}) == "TXN-00042"
        assert caso_mas_cercano_en_riesgo(carpeta, {"fraud_score": 75}) == "TXN-00089"

    def test_solo_mira_el_riesgo(self, carpeta):
        """Ni el metodo de pago ni el pais entran en la cuenta."""
        cripto = {"fraud_score": 79, "payment_method": "Cripto", "country": "COL"}
        assert caso_mas_cercano_en_riesgo(carpeta, cripto) == "TXN-00089"

    def test_a_igual_distancia_la_eleccion_es_reproducible(self, carpeta):
        """Score 6: a 2 de TXN-00051 y a 2 de TXN-00042. Siempre el mismo."""
        elegidos = {caso_mas_cercano_en_riesgo(carpeta, {"fraud_score": 6}) for _ in range(5)}
        assert len(elegidos) == 1

    def test_sin_score_no_se_rompe(self, carpeta):
        assert caso_mas_cercano_en_riesgo(carpeta, {}) is not None

    def test_una_carpeta_vacia_no_inventa_un_caso(self, tmp_path):
        vacia = tmp_path / "sin_nada"
        vacia.mkdir()
        assert caso_mas_cercano_en_riesgo(str(vacia), {"fraud_score": 5}) is None
        assert informe_de_ejemplo(str(vacia), {"fraud_score": 5}) is None

    def test_la_distancia_es_la_del_score(self):
        assert distancia_de_riesgo({"fraud_score": 27}, {"fraud_score": 8}) == 19

    def test_un_score_ilegible_manda_el_caso_al_final(self):
        assert distancia_de_riesgo({"fraud_score": "n/d"}, {"fraud_score": 8}) == float("inf")


class TestElEjemploSeDeclara:
    CASOS = TestElEjemploMasCercanoEnRiesgo.CASOS

    def _carpeta(self, tmp_path):
        c = tmp_path / "informes"
        c.mkdir()
        for txn, caso in self.CASOS.items():
            (c / f"report_x_{txn}.html").write_text(
                f"<html><body><h1>{txn}</h1></body></html>", encoding="utf-8"
            )
            (c / f"analisis_{txn}.json").write_text(
                json.dumps({"resolution": {"transaction_id": txn}, "judge": {},
                            "caso": {**caso, "transaction_id": txn}}), encoding="utf-8",
            )
        return str(c)

    def test_el_cartel_nombra_las_dos_transacciones(self, tmp_path):
        html, txn = informe_de_ejemplo(self._carpeta(tmp_path),
                                       {"id": "TXN-00004", "fraud_score": 7})
        assert "TXN-00004" in html and txn in html

    def test_dice_que_los_datos_son_del_otro_caso(self, tmp_path):
        html, txn = informe_de_ejemplo(self._carpeta(tmp_path),
                                       {"id": "TXN-00004", "fraud_score": 7})
        assert "no de TXN-00004" in html

    def test_devuelve_el_informe_entero_del_ejemplo(self, tmp_path):
        """Jamas los datos de una transaccion con la resolucion de otra."""
        html, txn = informe_de_ejemplo(self._carpeta(tmp_path),
                                       {"id": "TXN-00004", "fraud_score": 7})
        assert f"<h1>{txn}</h1>" in html

    def test_pedir_un_caso_guardado_usa_el_cartel_normal(self, tmp_path):
        html = informe_demo(self._carpeta(tmp_path), "TXN-00051")
        assert CARTEL in html
        assert "Pediste" not in html


class TestNoSeDisimulaLaFaltaDeN8n:
    """Pedir n8n y recibir el pipeline directo sería mentir sobre el origen.

    El informe se vería idéntico, y quien evalúa creería que pasó por los 29
    nodos de orquestación cuando no pasó. Un error explicado es mejor.
    """

    def test_sin_url_la_pagina_dice_que_falta(self):
        from api.app.routes.panel import _pagina_sin_n8n
        pagina = _pagina_sin_n8n("TXN-00051")
        assert "n8n URL" in pagina
        assert "no puede adivinar" in pagina

    def test_sin_url_ofrece_el_modo_directo_como_salida(self):
        from api.app.routes.panel import _pagina_sin_n8n
        assert "Directo" in _pagina_sin_n8n("TXN-00051")

    def test_si_no_responde_dice_a_donde_se_llamo(self):
        from api.app.routes.panel import _pagina_n8n_no_respondio
        pagina = _pagina_n8n_no_respondio("TXN-00051", "http://localhost:5678", False)
        assert "http://localhost:5678/webhook/chargeback-agent" in pagina

    def test_si_no_responde_aclara_que_no_se_corrio_lo_otro(self):
        from api.app.routes.panel import _pagina_n8n_no_respondio
        pagina = _pagina_n8n_no_respondio("TXN-00051", "http://x", False)
        assert "No se ejecuto el pipeline directo" in pagina

    def test_el_modo_prueba_recuerda_apretar_execute(self):
        """El webhook de test escucha una sola ejecucion: es el error mas comun."""
        from api.app.routes.panel import _pagina_n8n_no_respondio
        assert "Execute workflow" in _pagina_n8n_no_respondio("TXN-1", "http://x", True)

    def test_el_modo_produccion_recuerda_activar_el_workflow(self):
        from api.app.routes.panel import _pagina_n8n_no_respondio
        assert "activo" in _pagina_n8n_no_respondio("TXN-1", "http://x", False)

    def test_cada_modo_apunta_a_su_webhook(self):
        from api.app.routes.panel import _pagina_n8n_no_respondio
        prueba = _pagina_n8n_no_respondio("TXN-1", "http://x", True)
        prod = _pagina_n8n_no_respondio("TXN-1", "http://x", False)
        assert "webhook-test" in prueba
        assert "webhook-test" not in prod


class TestProcedenciaDelAnalisisGuardado:
    """Un informe guardado envejece: hay que decir de cuándo es.

    Los prompts y los umbrales cambian, y el resultado deja de ser el que el
    sistema produciría hoy. Declararlo es la diferencia entre un ejemplo y una
    foto vieja presentada como actual — y regenerarlos cuesta saldo de API.
    """

    CARPETA = str(Path(__file__).resolve().parents[2] / "data" / "informes_demo")

    def test_cada_analisis_guardado_declara_su_procedencia(self):
        import glob
        import json

        archivos = glob.glob(f"{self.CARPETA}/analisis_*.json")
        assert archivos, "no hay análisis guardados"
        for f in archivos:
            proc = json.loads(Path(f).read_text(encoding="utf-8")).get("procedencia")
            assert proc, f"{Path(f).name} no declara cuándo se generó"
            assert proc.get("generado"), f"{Path(f).name}: sin fecha"
            assert proc.get("prompts"), f"{Path(f).name}: sin versiones de prompt"

    def test_el_cartel_dice_cuando_y_con_que_version(self):
        html = precomputados.informe_demo(self.CARPETA, "TXN-00051")
        assert html is not None
        assert "Generado el" in html
        assert "resolution v3.0" in html
        assert "no es necesariamente el que produciria hoy" in html

    def test_sin_procedencia_el_cartel_no_inventa_nada(self):
        assert precomputados.linea_de_procedencia(None) == ""
        assert precomputados.linea_de_procedencia({}) == ""

    def test_la_procedencia_tambien_aparece_en_el_informe_sustituto(self):
        html = precomputados.informe_demo(self.CARPETA, "TXN-00051", solicitada="TXN-00004")
        assert "TXN-00004" in html and "Generado el" in html


class TestLaCorridaGratuitaViajaConSuModelo:
    """`POST /api/analyze/resolve` dice con que corrio, no solo que fue demo.

    El informe lo necesita para elegir el cartel, y quien pide el informe puede
    no ser el panel: n8n llama a la API derecho. Si el dato no viaja con la
    resolucion, el unico que puede corregirlo es el panel, y entonces todo lo
    que no pase por ahi sale mal rotulado. Es exactamente lo que pasaba.
    """

    @staticmethod
    def _app(monkeypatch, modelo):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.app.dependencies import get_db, get_modelos_service, get_settings
        from api.app.routes import analyze

        class ServicioFalso:
            def resolve(self, ctx):
                return {"transaction_id": "TXN-00051", "recommended_action": "REJECT",
                        "risk_level": "BLOCKER", "confidence": 0.9, "justification": "x"}

        class ModelosFalso:
            def servicio(self, demo=False):
                return ServicioFalso()

            def modelo_demo(self):
                return modelo

        app = FastAPI()
        app.include_router(analyze.router)
        settings = type("S", (), {"demo_mode": True, "demo_reports_path": "no-existe"})()
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_modelos_service] = lambda: ModelosFalso()
        app.dependency_overrides[get_db] = lambda: None
        app.dependency_overrides[analyze.get_resolution_service] = lambda: ServicioFalso()
        return TestClient(app)

    CASO = {
        "transaction_id": "TXN-00051", "motivo": "No reconoce la compra",
        "tx_data": {"id": "TXN-00051", "merchant": "X", "amount_usd": 10.0,
                    "payment_method": "Cripto", "country": "AR", "channel": "Web",
                    "fraud_score": 8, "client_id": "C1", "date": "2024-01-01"},
        "policies": [], "similar_cases": [], "logs": [],
        "merchant_risk": {}, "client_history": {},
    }

    def test_la_resolucion_dice_con_que_modelo_corrio(self, monkeypatch):
        modelo = {"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"}
        r = self._app(monkeypatch, modelo).post("/api/analyze/resolve", json=self.CASO)
        assert r.status_code == 200
        assert r.json()["demo"] is True
        assert r.json()["demo_modelo"] == modelo

    def test_sin_modelo_resuelto_no_se_inventa_uno(self, monkeypatch):
        """Preferible sin cartel de modelo que con uno que dice cualquier cosa."""
        r = self._app(monkeypatch, None).post("/api/analyze/resolve", json=self.CASO)
        assert r.json()["demo_modelo"] is None
