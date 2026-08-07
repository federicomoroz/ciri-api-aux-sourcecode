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


class TestElPanelLlevaAlFormulario:
    """Un caso que espera una persona no es un fallo de n8n.

    El panel muestra los informes en un `<iframe>` y descartaba toda respuesta
    que no fuera 200. Cuando el workflow empezo a redirigir al formulario de
    aprobacion (303), el panel lo leyo como «n8n no respondio» y ofrecio
    reintentar — justo cuando el sistema estaba haciendo lo correcto.

    Navegar y no incrustar: el formulario de n8n arma su URL de envio desde
    `window.location`, asi que copiar su HTML adentro del panel mandaria la
    decision del analista al origen equivocado.
    """

    URL = "http://localhost:5678/form-waiting/74?signature=abc&x=1"

    @property
    def pagina(self):
        from api.app.routes.panel import _pagina_hacia_el_formulario

        return _pagina_hacia_el_formulario("TXN-00011", self.URL)

    def test_navega_sola_al_formulario(self):
        assert 'http-equiv="refresh"' in self.pagina
        assert "form-waiting/74" in self.pagina

    def test_el_ampersand_de_la_firma_no_se_rompe(self):
        """Escapado a medias, la firma llega cortada y el formulario la rechaza."""
        assert "&amp;x=1" in self.pagina
        assert "<script" not in self.pagina, (
            "adentro de un <script> las entidades no se decodifican: el link viajaria roto"
        )

    def test_deja_el_link_a_la_vista(self):
        """El iframe puede no llegar: n8n y el panel pueden no estar en la misma red."""
        assert 'target="_blank"' in self.pagina
        assert self.pagina.count("form-waiting/74") == 2

    def test_dice_de_que_caso_se_trata(self):
        assert "TXN-00011" in self.pagina

    def test_un_destino_hostil_no_escapa_del_atributo(self):
        from api.app.routes.panel import _pagina_hacia_el_formulario

        sucio = _pagina_hacia_el_formulario("TXN-1", 'http://x/"><script>alert(1)</script>')
        assert "<script>alert" not in sucio


class TestElPipelineConN8nSeHabilitaCuandoResponde:
    """No se ofrece un modo que no va a poder correr.

    El selector traia «n8n Test» y «n8n Production» siempre elegibles, y lo que
    pasara despues dependia de una URL que podia no existir. Ahora arrancan
    deshabilitadas y se habilitan cuando la API confirma que llega.

    Quien verifica importa: antes lo probaba el NAVEGADOR con `fetch(no-cors)`,
    que responde otra pregunta —si vos llegas a esa URL—. Al webhook lo llama la
    API, que puede estar en otra red: con la API en un contenedor,
    `http://localhost:5678` abre el editor en tu browser y desde el contenedor
    es la API misma. El badge daba verde y la consulta moria.
    """

    @property
    def panel(self) -> str:
        from pathlib import Path

        return Path("api/app/reports/templates/test_panel.html").read_text(encoding="utf-8")

    def test_las_opciones_de_n8n_arrancan_deshabilitadas(self):
        html = self.panel
        assert '<option value="test" disabled>' in html
        assert '<option value="production" disabled>' in html
        assert '<option value="direct" selected>' in html, "Directo tiene que seguir siendo el default"

    def test_la_verificacion_la_hace_la_api(self):
        """Un chequeo que pasa mientras la llamada real falla es peor que ninguno."""
        html = self.panel
        assert "/api/panel/n8n-status" in html
        assert "mode: 'no-cors'" not in html, "volvio a probar desde el navegador"

    def test_se_habilitan_solo_si_la_api_llega(self):
        html = self.panel
        assert "if (d.available) {" in html
        assert "habilitarModosN8n(true" in html

    def test_si_deja_de_responder_se_vuelve_a_directo(self):
        """Dejarlo elegido prometeria una orquestacion que la consulta no tendra."""
        assert "if (!puede && usaN8n()) mode.value = 'direct';" in self.panel

    def test_se_explica_que_la_llamada_la_hace_la_api(self):
        assert "la llamada la hace la API, no tu navegador" in self.panel

    def test_tambien_se_deshabilita_si_n8n_se_cae_despues(self):
        """Verificar al pegar la URL no alcanza: puede caerse con el panel abierto.

        El chequeo periodico ponia el chip en rojo y dejaba el modo elegido,
        esperando una orquestacion que ya no estaba.
        """
        html = self.panel
        cuerpo = html[html.index("async function checkHealth"):html.index("async function loadLangfuseStats")]
        assert "habilitarModosN8n(" in cuerpo, "el chequeo periodico no toca el selector"
        assert "setInterval(checkHealth, 30_000)" in html

    def test_pide_una_url_publica_y_dice_por_que(self):
        """El texto viejo invitaba a pegar `localhost`, que es lo unico que no anda.

        Desde el panel publicado, al webhook lo llama la API —que corre en otra
        maquina— y no el navegador. Un n8n local le es inalcanzable por
        definicion, y no hay campo que lo arregle.
        """
        html = self.panel
        assert "URL PUBLICA" in html
        assert "n8n Cloud" in html
        assert "Si importaste el workflow en tu n8n, pega su URL" not in html

    def test_ofrece_la_salida_para_un_n8n_local(self):
        """Decir que no se puede sin decir que hacer es dejar a alguien varado."""
        assert "docker-compose" in self.panel

    def test_si_no_se_pudo_ni_preguntar_tampoco_se_ofrece(self):
        html = self.panel
        cuerpo = html[html.index("async function checkHealth"):html.index("async function loadLangfuseStats")]
        catch = cuerpo[cuerpo.rindex("} catch {"):]
        assert "habilitarModosN8n(false" in catch


class TestElPanelInformaElModeloQueCorrio:
    """La misma respuesta HTTP decia dos cosas contradictorias.

        x-modelo-gratuito: true
        x-usage-json: {"model":"claude-haiku-4-5-20251001","cost_usd":0.028295}

    El informe nombraba bien a Gemini; la cabecera cobraba 2,8 centavos por una
    corrida que costo cero, porque se pasaba `settings.llm_model` —el default de
    produccion— sin importar que modelo habia servido. El panel muestra ese
    numero en un badge.

    Es el mismo error que ya se corrigio en la tabla de tarifas: ahi se arreglo
    el precio, no el nombre del modelo con el que se lo calcula.
    """

    GEMINI = {"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"}
    PRODUCCION = "claude-haiku-4-5-20251001"

    @staticmethod
    def _pedir(demo_mode, modelo_demo):
        from types import SimpleNamespace

        from api.app.domain.models import AnalyzeRequest
        from api.app.routes.panel import _modelo_que_corrio

        req = AnalyzeRequest(transaction_id="TXN-1", motivo="x", demo_mode=demo_mode)
        settings = SimpleNamespace(
            llm_model=TestElPanelInformaElModeloQueCorrio.PRODUCCION, demo_mode=True,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            modelos_service=SimpleNamespace(modelo_demo=lambda: modelo_demo),
        )))
        return _modelo_que_corrio(req, settings, request)

    def test_en_demo_informa_el_modelo_gratuito(self):
        assert self._pedir(True, self.GEMINI) == "gemini-flash-lite-latest"

    def test_en_produccion_informa_el_documentado(self):
        assert self._pedir(False, self.GEMINI) == self.PRODUCCION

    def test_sin_free_tier_no_inventa_uno(self):
        """Ahi corre la configuracion documentada, y eso es lo que hay que decir."""
        assert self._pedir(True, None) == self.PRODUCCION

    def test_el_costo_de_una_corrida_gratuita_es_cero(self):
        """Con el nombre correcto, la tarifa ya lo resuelve."""
        from api.app.llm.pricing import estimar_costo_usd

        assert estimar_costo_usd(self._pedir(True, self.GEMINI), 20000, 3000) == 0.0
        assert estimar_costo_usd(self._pedir(False, self.GEMINI), 20000, 3000) > 0


class TestElStreamNoSeQuedaMudo:
    """El panel se colgaba sin emitir error en 2 de 5 corridas medidas.

    Los tres pasos del pipeline son llamadas al modelo y cada una puede tardar
    minutos. Entre evento y evento el stream quedaba callado, el navegador
    abortaba por su cuenta, y el mensaje culpaba al cold start de Render con el
    servicio ya caliente. En dos corridas ni siquiera llego un evento de error,
    porque el generador seguia bloqueado adentro del modelo.

    Con un latido cada pocos segundos el stream nunca queda mudo, y el plazo del
    cliente pasa a ser de INACTIVIDAD: abortar porque no llega nada es correcto;
    abortar porque el analisis tarda, no.
    """

    @staticmethod
    def _latidos_y_eventos(generador, cada_s=0.05):
        from api.app.routes.panel import _con_latido

        salida = list(_con_latido(generador, cada_s=cada_s))
        return ([s for s in salida if s.startswith(": latido")],
                [s for s in salida if not s.startswith(": latido")])

    def test_manda_senal_de_vida_mientras_un_paso_tarda(self):
        import time

        def lento():
            yield "data: {}\n\n"
            time.sleep(0.3)
            yield "data: {}\n\n"

        latidos, eventos = self._latidos_y_eventos(lento())
        assert latidos, "el stream se quedo mudo durante el paso lento"
        assert len(eventos) == 2, "se perdio algun evento real"

    def test_un_stream_rapido_no_se_llena_de_latidos(self):
        def rapido():
            yield "data: {}\n\n"

        latidos, eventos = self._latidos_y_eventos(rapido(), cada_s=5)
        assert latidos == []
        assert len(eventos) == 1

    def test_el_error_sigue_llegando(self):
        """Lo peor seria que el latido tape la falla que tiene que reportar."""
        import pytest as _pytest

        def revienta():
            yield "data: {}\n\n"
            raise RuntimeError("el modelo no respondio")

        with _pytest.raises(RuntimeError, match="no respondio"):
            self._latidos_y_eventos(revienta())

    def test_el_latido_es_un_comentario_sse(self):
        """El panel ignora todo lo que no empiece con `data: `, asi que no lo rompe."""
        import time

        def lento():
            yield "data: {}\n\n"
            time.sleep(0.2)

        latidos, _ = self._latidos_y_eventos(lento())
        assert all(s.startswith(":") and s.endswith("\n\n") for s in latidos)

    def test_el_cliente_espera_por_inactividad_y_no_por_plazo_fijo(self):
        from pathlib import Path

        panel = Path("api/app/reports/templates/test_panel.html").read_text(encoding="utf-8")
        cuerpo = panel[panel.index("async function _runStreaming("):panel.index("async function _runClassic(")]
        assert "reiniciarPlazo();" in cuerpo, "el plazo no se reinicia al recibir datos"


class TestElLatidoNoDejaUnHiloTrabajandoSolo:
    """El modo de falla que el latido introdujo, y que sus tests no cubrian.

    `_con_latido` corre el pipeline en un hilo. Cuando el navegador se
    desconecta, Starlette cierra este generador y el `with _pipeline_efimero`
    de aguas arriba cierra los clientes HTTP — **mientras el hilo sigue adentro
    de una llamada al modelo con esos mismos clientes**: tokens que se gastan
    despues de cerrar la pestania, y un «client has been closed» que muere en
    una cola que ya nadie lee.

    Los cuatro tests que escribi con el latido cubrian sus caminos felices.
    Ninguno dejaba de consumir el generador, que era el unico modo de falla que
    el cambio traia.
    """

    @staticmethod
    def _generador_lento(marca, espera=0.3):
        def gen():
            yield "data: {}\n\n"
            import time

            time.sleep(espera)
            marca.append("siguio")
            yield "data: {}\n\n"

        return gen()

    def test_cerrar_el_stream_espera_al_hilo(self):
        """Los clientes se cierran despues, no durante."""
        import threading

        from api.app.routes.panel import _con_latido

        marca = []
        antes = threading.active_count()
        g = _con_latido(self._generador_lento(marca), cada_s=0.05)
        next(g)
        g.close()
        assert threading.active_count() <= antes, "quedo un hilo corriendo tras cerrar"

    def test_no_se_empieza_el_paso_siguiente_si_nadie_escucha(self):
        import time

        from api.app.routes.panel import _con_latido

        pasos = []

        def gen():
            for i in range(4):
                pasos.append(i)
                yield "data: {}\n\n"
                time.sleep(0.05)

        g = _con_latido(gen(), cada_s=0.02)
        next(g)
        g.close()
        time.sleep(0.3)
        assert len(pasos) < 4, "el pipeline siguio recorriendo pasos sin cliente"

    def test_el_camino_normal_no_se_ve_afectado(self):
        from api.app.routes.panel import _con_latido

        def gen():
            yield 'data: {"step": "start"}' + chr(10) * 2
            yield 'data: {"step": "done"}' + chr(10) * 2

        salida = [s for s in _con_latido(gen(), cada_s=5) if not s.startswith(":")]
        assert len(salida) == 2


class TestElPanelNoAfirmaUnPlazoQueNoSeMidio:
    """El `null` del SLA llegaba al panel como «FUERA de SLA: nulld».

    El arreglo del SLA propago el null bien al nodo de n8n y no al panel: mismo
    campo, mismo lenguaje, otro consumidor. `ev.within_sla ? A : B` con null cae
    en el else, asi que la pantalla que se mira primero afirmaba exactamente el
    incumplimiento que el calculo existe para dejar de afirmar.
    """

    @property
    def panel(self) -> str:
        from pathlib import Path

        return Path("api/app/reports/templates/test_panel.html").read_text(encoding="utf-8")

    def test_distingue_el_null_del_incumplimiento(self):
        assert "ev.within_sla === null" in self.panel

    def test_lo_dice_en_castellano(self):
        assert "el plazo no se mide" in self.panel

    def test_el_evento_no_disimula_el_null(self):
        """`sla.get("within_sla", True)` no hacia nada: la clave existe con None."""
        from pathlib import Path

        pipeline = Path("api/app/services/pipeline.py").read_text(encoding="utf-8")
        bloque = pipeline[pipeline.index('elif name == "sla"'):]
        assert '"within_sla": sla.get("within_sla")' in bloque[:600]
