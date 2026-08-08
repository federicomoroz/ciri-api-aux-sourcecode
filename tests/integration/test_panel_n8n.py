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
        admin_api_key="", n8n_base_url="", api_url_para_n8n="", n8n_form_path="", demo_mode=False,
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


class TestElModoDemoNoSustituyeElOrquestador:
    """La misma regla, para el caso que faltaba: con el modo demo prendido.

    Los tests de arriba mandan `demo_mode: False`. Con el modo demo —que es el
    default del deploy— la rama de demo devolvia el informe antes de llegar al
    bloque de n8n, asi que elegir «n8n Production» en el panel daba un informe
    del pipeline directo. Medido contra la instancia local: 74 ejecuciones de
    n8n antes de la consulta y 74 despues.

    El modo demo es sobre plata, no sobre quien orquesta: los nodos llaman a
    esta misma API, que resuelve el modelo gratuito igual. No habia nada que
    ahorrar salteandolo.
    """

    DEMO = {**CUERPO, "demo_mode": True}

    def test_pedir_n8n_en_modo_demo_no_cae_al_directo(self, cliente):
        r = cliente.post("/api/panel/analyze", json=self.DEMO)
        assert r.status_code == 400, "devolvio un informe sin pasar por la orquestacion"
        assert "Falta la URL" in r.text

    def test_el_pipeline_directo_ni_se_toca(self, cliente):
        cliente.post("/api/panel/analyze", json=self.DEMO)
        app.state.pipeline_service.run.assert_not_called()

    def test_el_modo_directo_en_demo_si_corre(self, cliente):
        """Lo que no se puede sustituir es lo que se pidio explicitamente.

        `direct=1` es el default del selector y tiene que seguir andando sin
        n8n: es como se evalua el sistema sin instalar nada.
        """
        r = cliente.post("/api/panel/analyze?direct=1", json=self.DEMO)
        assert r.status_code != 400 or "Falta la URL" not in r.text


class TestElBadgeMiraLaUrlQueSeVaAUsar:
    """Un badge en verde seguido de «tu n8n no respondio» es peor que no tenerlo.

    El chequeo usaba `CB_N8N_BASE_URL` —la del servidor— y el analisis usaba la
    que el panel tuviera escrita. Con la API en un contenedor y el campo en
    `http://localhost:5678`, el ping daba OK contra `http://n8n:5678` y la
    consulta moria contra localhost, que dentro del contenedor es la API misma.
    La pagina se contradecia a si misma y no habia forma de verlo desde afuera.
    """

    def test_la_url_del_panel_gana_sobre_la_del_servidor(self, cliente):
        r = cliente.get("/api/panel/n8n-status?n8n_base_url=http://un-host-inventado:5678")
        assert r.json()["url"] == "http://un-host-inventado:5678"

    def test_sin_url_propia_sigue_usando_la_del_servidor(self, cliente):
        """El caso normal: el campo vacio y el servidor sabe donde esta n8n."""
        assert cliente.get("/api/panel/n8n-status").json()["configured"] is False


class TestSeExplicaQueLaLlamadaLaHaceLaApi:
    """La trampa mas comun, y la que no se ve desde el browser.

    En el browser `http://localhost:5678` abre el editor de n8n sin problemas,
    asi que parece la URL correcta. Pero quien llama es la API: si corre en un
    contenedor, su `localhost` es ella misma y n8n queda del otro lado.
    """

    @staticmethod
    def _pagina(base, servidor):
        from api.app.routes.panel import _pagina_n8n_no_respondio

        return _pagina_n8n_no_respondio("TXN-00016", base, False, servidor)

    def test_una_url_local_con_servidor_remoto_se_explica(self):
        h = self._pagina("http://localhost:5678", "http://n8n:5678")
        assert "la llama la API" in h
        assert "http://n8n:5678" in h, "no dice cual es la que si funciona"

    def test_dice_como_arreglarlo(self):
        assert "vacia el campo" in self._pagina("http://127.0.0.1:5678", "http://n8n:5678")

    def test_una_url_remota_no_recibe_el_aviso(self):
        """Ahi el problema es otro y mandarlo por esta pista seria desviar."""
        assert "la llama la API" not in self._pagina("https://x.app.n8n.cloud", "http://n8n:5678")

    def test_si_el_servidor_tambien_es_local_no_hay_nada_que_sugerir(self):
        """Todo en la misma maquina, sin contenedores: la URL no es el problema."""
        assert "la llama la API" not in self._pagina("http://localhost:5678", "http://localhost:5678")


class TestElBotonAnalizarFuncionaEnLaConfiguracionPublicada:
    """El camino principal del panel estaba roto justo en el deploy.

    `/api/panel/analyze-stream` cortaba con «API key requerida» cuando no habia
    clave de Anthropic, sin mirar el modo demo — que once lineas mas abajo esta
    perfectamente manejado. La instancia publicada tiene Gemini configurado y
    andando, y `?direct=1` corria entero; el SSE no. Y el panel usa SSE por
    defecto, asi que apretar «Analizar» sin tocar nada devolvia un error.

    El unico test del endpoint hacia `skip` sin clave de Anthropic y siempre
    mandaba una: la configuracion que corre en produccion era la unica sin
    cobertura. Esta clase es esa cobertura.
    """

    CUERPO_DEMO = {"transaction_id": TXN, "motivo": "No reconoce la compra", "demo_mode": True}

    @staticmethod
    def _cliente(in_memory_db_path, *, con_modelo_demo: bool, clave_servidor: str = ""):
        from api.app.data.db import Database
        from api.app.routes import panel as ruta_panel

        app.state.db = Database(in_memory_db_path)
        app.state.settings = SimpleNamespace(
            admin_api_key="", n8n_base_url="", api_url_para_n8n="", n8n_form_path="", demo_mode=True,
            llm_model="haiku", llm_model_resolution="", anthropic_api_key=clave_servidor,
            demo_reports_path="", report_cache_enabled=False,
        )
        app.state.pipeline_service = MagicMock()
        app.state.report_generator = MagicMock()
        app.state.tracer = MagicMock()
        app.state.contacto_n8n = ContactoN8n()
        app.state.modelos_service = MagicMock()
        app.state.modelos_service.modelo_demo.return_value = (
            {"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"}
            if con_modelo_demo else None
        )
        return TestClient(app, raise_server_exceptions=False), ruta_panel

    @staticmethod
    def _primer_evento(respuesta) -> str:
        """Un evento SSE termina en una linea en blanco."""
        return respuesta.text.split("\n\n")[0]

    def test_con_modelo_demo_no_pide_clave(self, in_memory_db_path):
        """Es la configuracion del deploy: sin clave de Anthropic y con free tier."""
        cliente, _ = self._cliente(in_memory_db_path, con_modelo_demo=True)
        with cliente as c:
            r = c.post("/api/panel/analyze-stream", json=self.CUERPO_DEMO)
        assert "API key requerida" not in r.text, (
            "el boton «Analizar» del panel publicado devuelve un error"
        )

    def test_sin_modelo_demo_y_sin_clave_si_la_pide(self, in_memory_db_path):
        """Ahi no hay con que correr, y decirlo es lo correcto."""
        cliente, _ = self._cliente(in_memory_db_path, con_modelo_demo=False)
        with cliente as c:
            r = c.post("/api/panel/analyze-stream", json=self.CUERPO_DEMO)
        assert "API key requerida" in self._primer_evento(r)

    def test_con_clave_del_servidor_tampoco_la_pide(self, in_memory_db_path):
        cliente, _ = self._cliente(
            in_memory_db_path, con_modelo_demo=False, clave_servidor="sk-ant-loquesea",
        )
        with cliente as c:
            r = c.post("/api/panel/analyze-stream", json=self.CUERPO_DEMO)
        assert "API key requerida" not in self._primer_evento(r)
