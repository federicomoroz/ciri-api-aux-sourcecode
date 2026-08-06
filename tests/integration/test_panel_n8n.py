"""El panel no disimula cuando la ejecución por n8n no puede ocurrir.

Es el escenario de la entrega: la API publicada no sabe —ni puede saber— dónde
corre el n8n de quien la evalúa. Si eligen ejecutar por n8n y no dicen dónde,
caer al pipeline directo devolvería un informe idéntico al real, y quien evalúa
creería que pasó por los 29 nodos de orquestación sin haber pasado. Un error
explicado vale más que un informe que miente sobre su origen.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.observability.contacto_n8n import ContactoN8n

TXN = "TXN-00051"
CUERPO = {"transaction_id": TXN, "motivo": "No reconoce la compra", "demo_mode": False}


@pytest.fixture
def cliente(in_memory_db_path):
    """Un panel cuyo servidor no tiene n8n configurado, como el publicado."""
    from api.app.data.db import Database

    app.state.db = Database(in_memory_db_path)
    app.state.settings = SimpleNamespace(
        admin_api_key="", n8n_base_url="", n8n_form_path="", demo_mode=False,
        llm_model="haiku", llm_model_resolution="", anthropic_api_key="clave",
        demo_reports_path="", report_cache_enabled=False,
    )
    app.state.pipeline_service = MagicMock()
    app.state.report_generator = MagicMock()
    app.state.tracer = MagicMock()
    # Lo mismo que arma el arranque real: sin esto el middleware no tiene
    # donde anotar y el panel nunca podria confirmar nada.
    app.state.contacto_n8n = ContactoN8n()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestPideLaUrlEnVezDeAdivinar:
    def test_sin_url_no_ejecuta_el_pipeline_directo(self, cliente):
        r = cliente.post("/api/panel/analyze", json=CUERPO)
        assert r.status_code == 400
        assert "Falta la URL" in r.text

    def test_el_pipeline_directo_ni_se_toca(self, cliente):
        """Si se hubiera ejecutado, el informe saldria y nadie notaria el enganio."""
        cliente.post("/api/panel/analyze", json=CUERPO)
        app.state.pipeline_service.run.assert_not_called()

    def test_dice_donde_se_pone_la_url(self, cliente):
        assert "n8n URL" in cliente.post("/api/panel/analyze", json=CUERPO).text

    def test_ofrece_el_modo_directo_como_alternativa(self, cliente):
        assert "Directo" in cliente.post("/api/panel/analyze", json=CUERPO).text

    def test_pedir_directo_explicitamente_sigue_andando(self, cliente):
        """La exigencia es solo para quien eligio n8n."""
        app.state.pipeline_service.run.return_value = ("<html>ok</html>", {})
        r = cliente.post("/api/panel/analyze?direct=true", json=CUERPO)
        assert r.status_code == 200
        app.state.pipeline_service.run.assert_called_once()


class TestUnN8nQueNoContesta:
    def test_responde_502_y_no_un_informe(self, cliente):
        r = cliente.post(
            "/api/panel/analyze?n8n_base_url=http://127.0.0.1:9",
            json=CUERPO,
        )
        assert r.status_code == 502
        assert "no respondio" in r.text

    def test_tampoco_ejecuta_el_pipeline_directo(self, cliente):
        cliente.post("/api/panel/analyze?n8n_base_url=http://127.0.0.1:9", json=CUERPO)
        app.state.pipeline_service.run.assert_not_called()

    def test_dice_a_que_direccion_llamo(self, cliente):
        r = cliente.post("/api/panel/analyze?n8n_base_url=http://127.0.0.1:9", json=CUERPO)
        assert "http://127.0.0.1:9/webhook/chargeback-agent" in r.text


class TestConfirmaQueElN8nLlego:
    """El workflow marca su primera llamada; el panel lo usa para confirmar.

    Se mira una cabecera puesta por el workflow y no el User-Agent, porque n8n
    usa el de axios y cualquier script lo manda igual: confirmar de más sería
    peor que no confirmar nada.
    """

    from api.app.domain.constants import N8N_ORIGIN_HEADER as CABECERA

    def test_al_principio_no_hay_nada_que_confirmar(self, cliente):
        d = cliente.get("/api/panel/n8n-status").json()
        assert d["ultimo_contacto_hace_s"] is None
        assert d["contactos"] == 0

    def test_una_llamada_con_la_cabecera_queda_registrada(self, cliente):
        cliente.get(f"/api/transactions/{TXN}", headers={self.CABECERA: "orquestador"})
        d = cliente.get("/api/panel/n8n-status").json()
        assert d["contactos"] == 1
        assert d["ultimo_contacto_hace_s"] is not None

    def test_una_llamada_sin_la_cabecera_no_cuenta(self, cliente):
        """El panel mismo llama a la API todo el tiempo; eso no es n8n."""
        cliente.get(f"/api/transactions/{TXN}")
        assert cliente.get("/api/panel/n8n-status").json()["contactos"] == 0

    def test_la_antiguedad_es_reciente_recien_registrada(self, cliente):
        cliente.get(f"/api/transactions/{TXN}", headers={self.CABECERA: "orquestador"})
        assert cliente.get("/api/panel/n8n-status").json()["ultimo_contacto_hace_s"] < 5

    def test_se_informa_aunque_no_haya_n8n_configurado(self, cliente):
        """Es justo el caso de la API publicada: sin n8n propio, pero visitada."""
        cliente.get(f"/api/transactions/{TXN}", headers={self.CABECERA: "orquestador"})
        d = cliente.get("/api/panel/n8n-status").json()
        assert d["configured"] is False
        assert d["contactos"] == 1
