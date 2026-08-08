"""Los seis caminos de `panel_analyze`, fijados antes de refactorizarlo.

Tests de caracterizacion: no juzgan si el comportamiento es el deseable, afirman
cual es. Existen porque la fase 4 del refactor SOLID convierte las 122 lineas de
`panel_analyze` en un `Modo` + un despacho, y `routes/panel.py` es el archivo con
menos cobertura del proyecto (77%): hay ramas que ningun test estaba mirando.

**Lo que mas importa que quede fijado es la precedencia**, no cada respuesta por
separado. El orden de las ramas encodea una correccion: pedir n8n gana sobre el
modo demo. Cuando no era asi, elegir «n8n Production» en el panel devolvia un
informe del pipeline directo — parecia venir de la orquestacion sin haber pasado
por ella. Un `resolver_modo` que reordene las condiciones reintroduce ese defecto
sin romper ningun otro test.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app.main import app
from api.app.observability.contacto_n8n import ContactoN8n

TXN = "TXN-00051"
OTRO = "TXN-99999"          # no tiene informe guardado
GRATIS = {"proveedor": "gemini", "modelo": "gemini-flash-lite-latest"}


def _cuerpo(txn: str = TXN, **extra) -> dict:
    return {"transaction_id": txn, "motivo": "No reconoce la compra", **extra}


@pytest.fixture
def guardados(tmp_path):
    """Una carpeta con un informe guardado, con el nombre que el codigo espera."""
    carpeta = tmp_path / "informes"
    carpeta.mkdir()
    (carpeta / f"report_blocker_{TXN}.html").write_text(
        "<html><body>informe guardado</body></html>", encoding="utf-8",
    )
    return str(carpeta)


@pytest.fixture
def panel(in_memory_db_path, guardados):
    """El panel armado como en produccion, con cada costura bajo control.

    Devuelve (cliente, ajustes) para que cada test mueva solo la palanca que le
    interesa: el modo demo, el modelo gratuito, la clave del servidor o la URL de
    n8n.
    """
    from api.app.data.db import Database

    ajustes = SimpleNamespace(
        admin_api_key="", n8n_base_url="", n8n_form_path="", demo_mode=True,
        llm_model="haiku", llm_model_resolution="", anthropic_api_key="",
        demo_reports_path=guardados, report_cache_enabled=False,
    )
    app.state.db = Database(in_memory_db_path)
    app.state.settings = ajustes
    app.state.pipeline_service = MagicMock()
    app.state.report_generator = MagicMock()
    app.state.tracer = MagicMock()
    app.state.contacto_n8n = ContactoN8n()

    modelos = MagicMock()
    modelos.modelo_demo.return_value = None      # sin free tier, salvo que el test lo cambie
    app.state.modelos_service = modelos

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, ajustes, modelos


# ── 1. Demo grabado ──────────────────────────────────────────────────────────

class TestDemoSinModeloGratuito:
    """Sin free tier configurado, el modo demo sirve el analisis guardado."""

    def test_devuelve_el_informe_guardado(self, panel):
        cliente, _, _ = panel
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo())
        assert r.status_code == 200
        assert "informe guardado" in r.text

    def test_no_toca_el_pipeline(self, panel):
        cliente, _, _ = panel
        cliente.post("/api/panel/analyze?direct=1", json=_cuerpo())
        app.state.pipeline_service.run.assert_not_called()

    def test_un_caso_sin_informe_guardado_no_es_un_error(self, panel):
        """Devuelve 200 con una pagina que lo explica, no un 404."""
        cliente, _, _ = panel
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo(OTRO))
        assert r.status_code == 200


# ── 2 y 3. Demo que ejecuta, y su respaldo ───────────────────────────────────

class TestDemoConModeloGratuito:
    """Con free tier, el modo demo corre de verdad: cuesta lo mismo y no envejece."""

    def test_corre_el_pipeline_en_vez_de_recitar(self, panel, monkeypatch):
        cliente, _, modelos = panel
        modelos.modelo_demo.return_value = GRATIS
        corridas = []
        monkeypatch.setattr(
            "api.app.routes.panel._correr_directo",
            lambda *a, **k: corridas.append(1) or __import__(
                "fastapi.responses", fromlist=["HTMLResponse"],
            ).HTMLResponse(content="<html>corrido</html>", status_code=200),
        )
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo())
        assert corridas, "con modelo gratuito tiene que ejecutar, no servir la grabacion"
        assert r.status_code == 200

    def test_si_falla_cae_al_guardado_y_lo_declara(self, panel, monkeypatch):
        """La cabecera es lo que distingue «cayo al respaldo» de «anduvo»."""
        cliente, _, modelos = panel
        modelos.modelo_demo.return_value = GRATIS

        def revienta(*_a, **_k):
            raise RuntimeError("free tier agotado")

        monkeypatch.setattr("api.app.routes.panel._correr_directo", revienta)
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo())
        assert r.status_code == 200
        assert "informe guardado" in r.text
        assert "free tier agotado" in r.headers.get("X-Demo-Fallback", "")


# ── 4. Efimero: el visitante trae lo suyo ────────────────────────────────────

class TestCuandoElVisitanteTraeLoSuyo:
    """Clave propia o modelo elegido: la configuracion es de esa corrida."""

    @pytest.mark.parametrize("extra", [
        {"api_key": "sk-ant-propia"},
        {"modelos": {"judge": {"proveedor": "groq", "modelo": "llama-3.3-70b"}}},
    ])
    def test_arma_un_pipeline_efimero(self, panel, monkeypatch, extra):
        cliente, _, _ = panel
        usados = []
        monkeypatch.setattr(
            "api.app.routes.panel._pipeline_efimero",
            lambda base, request, **k: usados.append(k) or _contexto_falso(),
        )
        cliente.post(
            "/api/panel/analyze?direct=1", json=_cuerpo(demo_mode=False, **extra),
        )
        assert usados, "no armo el pipeline de la peticion"

    @pytest.mark.parametrize("extra", [
        {"api_key": "sk-ant-propia"},
        {"modelos": {"judge": {"proveedor": "groq", "modelo": "llama-3.3-70b"}}},
    ])
    def test_con_el_demo_del_servidor_encendido_gana_el_guardado(self, panel, monkeypatch, extra):
        """Comportamiento actual, fijado tal como es — no como deberia ser.

        Si la peticion no dice `demo_mode`, decide el servidor. Con el demo
        encendido, la rama del informe guardado se evalua ANTES que la del
        pipeline efimero, asi que la clave o el modelo que trajo el visitante se
        descartan sin aviso y recibe la grabacion.

        Por el panel no se llega: el campo de la clave se esconde en modo demo.
        Por la API si. Queda registrado para que el refactor no lo cambie sin
        querer — y si algun dia se decide cambiarlo, que sea con este test
        delante y no por accidente.
        """
        cliente, _, _ = panel
        monkeypatch.setattr(
            "api.app.routes.panel._pipeline_efimero",
            lambda *a, **k: pytest.fail("armo el pipeline efimero: la precedencia cambio"),
        )
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo(**extra))
        assert r.status_code == 200
        assert "informe guardado" in r.text


def _contexto_falso():
    """Un context manager que entrega un pipeline mock."""
    from contextlib import contextmanager

    @contextmanager
    def _cm():
        yield MagicMock()

    return _cm()


# ── 5. n8n: se pide explicitamente y no se disimula ──────────────────────────

class TestPedirN8nGanaSobreElModoDemo:
    """La precedencia que el refactor tiene que preservar.

    Es la unica eleccion del panel que no se puede ignorar en silencio: el
    default del selector es «Directo», asi que pedir n8n es deliberado. Cuando el
    modo demo devolvia antes de llegar a este bloque, quien elegia «n8n
    Production» recibia un informe del pipeline directo.
    """

    def test_en_modo_demo_pedir_n8n_no_devuelve_el_guardado(self, panel):
        cliente, _, _ = panel                     # demo_mode=True, sin URL de n8n
        r = cliente.post("/api/panel/analyze", json=_cuerpo())
        assert r.status_code == 400, (
            "el modo demo se adelanto al pedido de n8n: devolvio un informe que "
            "parece venir de la orquestacion sin haber pasado por ella"
        )
        assert "informe guardado" not in r.text

    def test_sin_url_lo_dice(self, panel):
        cliente, _, _ = panel
        assert "Falta la URL" in cliente.post("/api/panel/analyze", json=_cuerpo()).text

    def test_con_url_que_no_responde_es_502(self, panel, monkeypatch):
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = "http://127.0.0.1:1"
        monkeypatch.setattr(
            "api.app.routes.panel._try_n8n",
            _corutina_que_devuelve(None),
        )
        r = cliente.post("/api/panel/analyze", json=_cuerpo())
        assert r.status_code == 502

    def test_con_url_que_responde_devuelve_lo_de_n8n(self, panel, monkeypatch):
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = "http://127.0.0.1:1"
        monkeypatch.setattr(
            "api.app.routes.panel._try_n8n",
            _corutina_que_devuelve("<html>vino de n8n</html>"),
        )
        r = cliente.post("/api/panel/analyze", json=_cuerpo())
        assert r.status_code == 200
        assert "vino de n8n" in r.text


def _corutina_que_devuelve(valor):
    async def _falsa(*_a, **_k):
        return valor

    return _falsa


# ── 6. Produccion sin clave ──────────────────────────────────────────────────

class TestProduccionSinClave:
    """Ejecutar de verdad requiere una credencial, y se dice cual falta."""

    def test_es_400_y_no_un_500(self, panel):
        cliente, ajustes, _ = panel
        ajustes.demo_mode = False
        ajustes.anthropic_api_key = ""
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo())
        assert r.status_code == 400
        assert "API key" in r.text

    def test_con_clave_del_servidor_si_corre(self, panel, monkeypatch):
        cliente, ajustes, _ = panel
        ajustes.demo_mode = False
        ajustes.anthropic_api_key = "sk-ant-del-servidor"
        from fastapi.responses import HTMLResponse

        monkeypatch.setattr(
            "api.app.routes.panel._correr_directo",
            lambda *a, **k: HTMLResponse(content="<html>produccion</html>", status_code=200),
        )
        r = cliente.post("/api/panel/analyze?direct=1", json=_cuerpo())
        assert r.status_code == 200
        assert "produccion" in r.text


# ── El streaming: los caminos de error ───────────────────────────────────────

class TestElAnalisisPorStreamingCuandoFalla:
    """Cada fallo dice algo distinto, y esa distincion es lo que se fija.

    Son las ramas que menos se ejercitaban y las que mas cuesta notar rotas: el
    panel muestra *un* mensaje de error igual, y solo leyendo cual se sabe si el
    sistema entendio lo que paso.
    """

    def _eventos(self, respuesta) -> list[dict]:
        import json as _json

        return [
            _json.loads(linea[6:])
            for linea in respuesta.text.splitlines()
            if linea.startswith("data: ")
        ]

    def test_sin_con_que_correr_lo_dice_antes_de_empezar(self, panel):
        cliente, ajustes, modelos = panel
        ajustes.anthropic_api_key = ""
        modelos.modelo_demo.return_value = None
        r = cliente.post("/api/panel/analyze-stream", json=_cuerpo(demo_mode=False))
        assert any("API key requerida" in e.get("message", "") for e in self._eventos(r))

    def test_una_clave_invalida_no_se_confunde_con_falta_de_saldo(self, panel):
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = "sk-ant-rota"
        _revienta_el_stream(panel, "authentication_error: invalid x-api-key")
        eventos = self._eventos(
            cliente.post("/api/panel/analyze-stream", json=_cuerpo(demo_mode=False)),
        )
        mensaje = " ".join(e.get("message", "") for e in eventos)
        assert "sk-ant-" in mensaje, "no reconocio que el problema era la clave"

    def test_sin_saldo_cae_al_informe_guardado(self, panel):
        """Un informe viejo declarado como tal vale mas que un error."""
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = "sk-ant-sin-credito"
        _revienta_el_stream(panel, "credit balance is too low")
        eventos = self._eventos(
            cliente.post("/api/panel/analyze-stream", json=_cuerpo(demo_mode=False)),
        )
        final = [e for e in eventos if e.get("step") == "done"]
        assert final and "informe guardado" in final[0].get("html", "")

    def test_un_caso_sin_informe_propio_igual_recibe_el_mas_parecido(self, panel):
        """El respaldo no exige que el caso pedido tenga su informe guardado.

        `_html_demo` cae al caso guardado mas cercano en riesgo, y el propio
        informe aclara de que transaccion es. Devolver algo util y declarado vale
        mas que un error — por eso la rama de «no hay nada que mostrar» solo se
        alcanza con la carpeta vacia, que es el test de abajo.
        """
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = "sk-ant-sin-credito"
        _revienta_el_stream(panel, "credit balance is too low")
        eventos = self._eventos(
            cliente.post("/api/panel/analyze-stream", json=_cuerpo(OTRO, demo_mode=False)),
        )
        final = [e for e in eventos if e.get("step") == "done"]
        assert final and "informe guardado" in final[0].get("html", "")

    def test_sin_nada_guardado_es_un_error_explicado(self, panel, tmp_path):
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = "sk-ant-sin-credito"
        vacia = tmp_path / "sin_informes"
        vacia.mkdir()
        ajustes.demo_reports_path = str(vacia)
        _revienta_el_stream(panel, "credit balance is too low")
        eventos = self._eventos(
            cliente.post("/api/panel/analyze-stream", json=_cuerpo(demo_mode=False)),
        )
        mensaje = " ".join(e.get("message", "") for e in eventos)
        assert "saldo" in mensaje and "no tiene informe" in mensaje

    def test_un_fallo_cualquiera_no_filtra_el_detalle(self, panel):
        """El mensaje del panel manda a los logs; el detalle no va a pantalla."""
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = "sk-ant-ok"
        _revienta_el_stream(panel, "psycopg2.OperationalError: FATAL password")
        eventos = self._eventos(
            cliente.post("/api/panel/analyze-stream", json=_cuerpo(demo_mode=False)),
        )
        mensaje = " ".join(e.get("message", "") for e in eventos)
        assert "password" not in mensaje
        assert "logs del servidor" in mensaje


def _revienta_el_stream(panel, texto: str) -> None:
    """Hace que el pipeline emita un paso y despues falle con ese mensaje.

    Se le pone al mock que ya esta en `app.state` y no a la clase real: la ruta
    resuelve el pipeline por inyeccion, asi que parchear `PipelineService` no
    llega a la instancia que se usa.
    """
    _, _, _modelos = panel

    def _falso(*_a, **_k):
        yield "start", {"transaction_id": TXN}
        raise RuntimeError(texto)

    app.state.pipeline_service.run_streaming = _falso


# ── n8n derivando a una persona ──────────────────────────────────────────────

class TestCuandoN8nDerivaAUnaPersona:
    """Un caso que necesita analista no es un fallo de n8n.

    El workflow redirige al formulario del nodo Wait. Sin esta rama el panel lo
    leia como «n8n no respondio» y ofrecia reintentar, justo cuando el sistema
    estaba haciendo lo correcto.
    """

    @pytest.mark.parametrize("codigo", [301, 302, 303, 307, 308])
    def test_lleva_al_formulario_en_vez_de_avisar_un_fallo(self, panel, monkeypatch, codigo):
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = "http://n8n.local"
        monkeypatch.setattr(
            "api.app.routes.panel.httpx.AsyncClient", _n8n_que_responde(
                codigo, {"location": "http://n8n.local/form/abc"},
            ),
        )
        r = cliente.post("/api/panel/analyze", json=_cuerpo())
        assert r.status_code == 200
        assert "form/abc" in r.text

    def test_una_redireccion_sin_destino_no_es_un_formulario(self, panel, monkeypatch):
        """Sin Location no hay a donde llevar a nadie: se trata como fallo."""
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = "http://n8n.local"
        monkeypatch.setattr(
            "api.app.routes.panel.httpx.AsyncClient", _n8n_que_responde(302, {}),
        )
        assert cliente.post("/api/panel/analyze", json=_cuerpo()).status_code == 502


def _n8n_que_responde(status: int, headers: dict):
    class Respuesta:
        status_code = status

        def __init__(self):
            self.headers = headers
            self.text = ""

        def json(self):
            return {}

    class ClienteFalso:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return Respuesta()

    return ClienteFalso


# ── Las rutas de estado que el panel consulta al cargar ──────────────────────

class TestLoQueElPanelPreguntaAlArrancar:
    """De estas tres respuestas dependen el toggle, el badge y el catalogo.

    Son rutas chicas y por eso nadie las miraba. Si el refactor cambia una forma
    —una clave que se renombra, un booleano que pasa a string— el panel no
    revienta: se dibuja mal, que es peor porque nadie lo nota.
    """

    def test_dice_si_el_servidor_tiene_clave_propia(self, panel):
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = "sk-ant-del-servidor"
        assert cliente.get("/api/panel/server-key-status").json() == {"has_server_key": True}

    def test_sin_clave_lo_dice_tambien(self, panel):
        cliente, ajustes, _ = panel
        ajustes.anthropic_api_key = ""
        assert cliente.get("/api/panel/server-key-status").json() == {"has_server_key": False}

    def test_el_estado_del_demo_declara_si_ejecuta_o_recita(self, panel):
        cliente, _, modelos = panel
        modelos.modelo_demo.return_value = GRATIS
        modelos.vigente.return_value = {
            "policies": {"proveedor": "anthropic", "modelo": "haiku", "titulo": "Politicas"},
        }
        cuerpo = cliente.get("/api/panel/demo-status").json()
        assert cuerpo["demo_ejecuta"] is True
        assert cuerpo["demo_modelo"] == GRATIS
        assert cuerpo["produccion"]["policies"]["modelo"] == "haiku"

    def test_sin_modelo_de_demo_el_respaldo_son_los_casos_guardados(self, panel):
        cliente, _, modelos = panel
        modelos.modelo_demo.return_value = None
        cuerpo = cliente.get("/api/panel/demo-status").json()
        assert cuerpo["demo_ejecuta"] is False
        assert TXN in cuerpo["casos"]

    def test_si_no_se_puede_resolver_el_modelo_del_demo_no_revienta(self, panel):
        """El panel tiene que cargar aunque la configuracion de modelos falle."""
        cliente, _, modelos = panel
        modelos.modelo_demo.side_effect = RuntimeError("base caida")
        cuerpo = cliente.get("/api/panel/demo-status").json()
        assert cuerpo["demo_modelo"] is None
        assert cuerpo["demo_ejecuta"] is False

    def test_sin_url_de_n8n_el_badge_dice_no_configurado(self, panel):
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = ""
        cuerpo = cliente.get("/api/panel/n8n-status").json()
        assert cuerpo["configured"] is False and cuerpo["available"] is False

    def test_la_url_del_panel_le_gana_a_la_del_servidor(self, panel):
        """Chequear una y llamar a la otra daba un badge verde y despues un 502."""
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = "http://del-servidor"
        cuerpo = cliente.get(
            "/api/panel/n8n-status?n8n_base_url=http://del-panel",
        ).json()
        assert cuerpo["url"] == "http://del-panel"

    def test_deriva_la_url_del_formulario_hitl(self, panel):
        cliente, ajustes, _ = panel
        ajustes.n8n_base_url = "http://n8n.local"
        ajustes.n8n_form_path = "form/abc"          # sin barra inicial, a proposito
        cuerpo = cliente.get("/api/panel/n8n-status").json()
        assert cuerpo["form_url"] == "http://n8n.local/form/abc"
        assert cuerpo["form_test_url"] == "http://n8n.local/form-test/abc"
