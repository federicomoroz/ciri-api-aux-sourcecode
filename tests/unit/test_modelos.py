"""Elegir el modelo de cada paso, sin deploy.

El pipeline hace tres llamadas y cada una es una tarea distinta. La eleccion
vive en SQLite —el panel la edita— y el default en `constants.py`. Lo que estos
tests fijan es lo que hace que eso sea seguro y util: que lo guardado pise al
default paso por paso, que cambiar uno no arrastre a los otros, que el cliente
se renueve al guardar, y que **las claves no se guarden nunca**.
"""

from unittest.mock import MagicMock

import pytest

from api.app.domain.constants import (
    PASO_JUEZ,
    PASO_POLITICAS,
    PASO_RESOLUCION,
    PASOS_DEL_PIPELINE,
)
from api.app.services.modelos import ModelosService


class DBFalsa:
    def __init__(self):
        self.filas = {}

    def get_modelos(self):
        return dict(self.filas)

    def save_modelo(self, paso, proveedor, modelo):
        self.filas[paso] = {"proveedor": proveedor, "modelo": modelo, "actualizado": "2026-08-07"}

    def reset_modelos(self):
        self.filas.clear()


def settings_falsas(**extra):
    base = {
        "llm_provider": "anthropic",
        "llm_model": "claude-haiku-4-5-20251001",
        "llm_model_resolution": "claude-sonnet-4-6",
        "anthropic_api_key": "clave-del-servidor",
        "llm_api_key": "",
        "llm_api_keys": {},
        "demo_provider": "",
        "demo_model": "",
        "llm_max_retries": 2,
        "llm_base_url": "",
    }
    base.update(extra)

    class S:
        def __init__(self, d):
            self.__dict__.update(d)

        def model_copy(self, update=None):
            return S({**self.__dict__, **(update or {})})

    return S(base)


class ManagerFalso:
    """Devuelve marcas en vez de clientes, para poder inspeccionarlas.

    Es el `LLMManager` real reducido a lo que este servicio le pide: una clave
    por proveedor y un cliente por (proveedor, modelo).
    """

    def __init__(self, settings):
        self.settings = settings
        self.tracer = MagicMock()
        self.invalidado = 0

    def clave_de(self, proveedor):
        por_proveedor = (self.settings.llm_api_keys or {}).get(proveedor, "")
        if por_proveedor:
            return por_proveedor
        if proveedor == "anthropic":
            return self.settings.anthropic_api_key
        return self.settings.llm_api_key

    def hay_clave(self, proveedor):
        return bool(self.clave_de(proveedor))

    def cliente(self, proveedor, modelo, api_key=""):
        return MagicMock(
            proveedor=proveedor, modelo=modelo,
            api_key=api_key or self.clave_de(proveedor),
        )

    def invalidar(self):
        self.invalidado += 1


def _servicio(**extra):
    s = settings_falsas(**extra)
    return ModelosService(DBFalsa(), s, ManagerFalso(s))


@pytest.fixture
def servicio():
    return _servicio()


class TestConfiguracionVigente:
    def test_sin_nada_guardado_manda_el_default(self, servicio):
        vigente = servicio.vigente()
        assert vigente[PASO_POLITICAS]["modelo"] == "claude-haiku-4-5-20251001"
        assert vigente[PASO_RESOLUCION]["modelo"] == "claude-sonnet-4-6"
        assert vigente[PASO_JUEZ]["modelo"] == "claude-sonnet-4-6"
        assert not any(v["personalizado"] for v in vigente.values())

    def test_lo_guardado_pisa_al_default(self, servicio):
        servicio.guardar(PASO_JUEZ, "groq", "llama-3.3-70b-versatile")
        vigente = servicio.vigente()
        assert vigente[PASO_JUEZ]["proveedor"] == "groq"
        assert vigente[PASO_JUEZ]["personalizado"] is True

    def test_cambiar_un_paso_no_arrastra_a_los_otros(self, servicio):
        """Es todo el punto: tres llamadas distintas, tres decisiones distintas."""
        servicio.guardar(PASO_JUEZ, "groq", "llama-3.3-70b-versatile")
        vigente = servicio.vigente()
        assert vigente[PASO_POLITICAS]["proveedor"] == "anthropic"
        assert vigente[PASO_RESOLUCION]["proveedor"] == "anthropic"
        assert not vigente[PASO_POLITICAS]["personalizado"]

    def test_cada_paso_trae_su_descripcion(self, servicio):
        for paso, cfg in servicio.vigente().items():
            assert cfg["titulo"] and cfg["detalle"] and cfg["prompt"], paso

    def test_estan_los_tres_pasos_y_solo_esos(self, servicio):
        assert set(servicio.vigente()) == set(PASOS_DEL_PIPELINE)

    def test_restablecer_vuelve_al_default(self, servicio):
        servicio.guardar(PASO_JUEZ, "groq", "x")
        servicio.restablecer()
        assert not any(v["personalizado"] for v in servicio.vigente().values())

    def test_un_paso_inexistente_se_rechaza(self, servicio):
        with pytest.raises(ValueError, match="paso desconocido"):
            servicio.guardar("inventado", "groq", "x")

    def test_un_modelo_vacio_se_rechaza(self, servicio):
        with pytest.raises(ValueError, match="vacio"):
            servicio.guardar(PASO_JUEZ, "groq", "   ")

    def test_si_la_base_falla_se_cae_al_default_en_vez_de_romper(self, servicio):
        servicio.db.get_modelos = MagicMock(side_effect=RuntimeError("sqlite caido"))
        vigente = servicio.vigente()
        assert vigente[PASO_JUEZ]["modelo"] == "claude-sonnet-4-6"


class TestClientes:
    def test_el_cliente_usa_el_proveedor_y_modelo_del_paso(self, servicio):
        servicio.guardar(PASO_JUEZ, "groq", "llama-3.3-70b-versatile")
        cliente = servicio.cliente(PASO_JUEZ)
        assert cliente.proveedor == "groq"
        assert cliente.modelo == "llama-3.3-70b-versatile"

    def test_guardar_le_avisa_al_manager(self, servicio):
        """Cambio la configuracion: los clientes que tenia cacheados ya no sirven."""
        servicio.guardar(PASO_JUEZ, "groq", "otro")
        assert servicio.manager.invalidado == 1

    def test_dos_pasos_distintos_dan_clientes_distintos(self, servicio):
        servicio.guardar(PASO_JUEZ, "groq", "llama")
        assert servicio.cliente(PASO_JUEZ) is not servicio.cliente(PASO_POLITICAS)


class TestLasClavesNoSeGuardan:
    """Una instancia publica que persistiera claves ajenas es un incidente."""

    def test_la_clave_de_la_peticion_no_toca_la_base(self, servicio):
        servicio.cliente(PASO_JUEZ, api_key="sk-del-visitante")
        assert servicio.db.get_modelos() == {}

    def test_el_cliente_con_clave_propia_no_se_cachea(self, servicio):
        propio = servicio.cliente(PASO_JUEZ, api_key="sk-del-visitante")
        del_servidor = servicio.cliente(PASO_JUEZ)
        assert propio is not del_servidor
        assert del_servidor.api_key == "clave-del-servidor"

    def test_la_clave_del_visitante_no_pisa_la_del_proceso(self, servicio):
        servicio.cliente(PASO_JUEZ, api_key="sk-del-visitante")
        assert servicio.settings.anthropic_api_key == "clave-del-servidor"

    def test_la_clave_de_la_peticion_llega_al_cliente(self, servicio):
        assert servicio.cliente(PASO_JUEZ, api_key="sk-del-visitante").api_key == "sk-del-visitante"


class TestCatalogo:
    def test_marca_cuales_son_gratis(self, servicio):
        gratis = {p["id"] for p in servicio.catalogo() if p["gratis"]}
        assert "groq" in gratis and "gemini" in gratis
        assert "anthropic" not in gratis

    def test_dice_cual_tiene_clave_cargada(self, servicio):
        catalogo = {p["id"]: p for p in servicio.catalogo()}
        assert catalogo["anthropic"]["tiene_clave"] is True
        assert catalogo["groq"]["tiene_clave"] is False

    def test_cada_proveedor_sugiere_al_menos_un_modelo(self, servicio):
        assert all(p["modelos"] for p in servicio.catalogo())

    def test_los_compatibles_traen_su_base_url(self, servicio):
        catalogo = {p["id"]: p for p in servicio.catalogo()}
        assert catalogo["groq"]["base_url"].startswith("https://")
        assert catalogo["anthropic"]["base_url"] == "", "Anthropic no va por el cliente compatible"


class TestElPanelSabeDondeSacarLaClave:
    """Elegir un proveedor sin decir de donde sale su clave deja a medias.

    El campo de API key del panel decia «Anthropic» y pedia `sk-ant-...` sin
    importar que proveedor estuviera configurado.
    """

    def test_cada_proveedor_dice_donde_sacar_la_clave(self, servicio):
        for p in servicio.catalogo():
            assert p["consola"].startswith("https://"), p["id"]
            assert p["formato_clave"], p["id"]

    def test_los_formatos_no_son_todos_el_de_anthropic(self, servicio):
        formatos = {p["id"]: p["formato_clave"] for p in servicio.catalogo()}
        assert formatos["anthropic"].startswith("sk-ant")
        assert formatos["groq"].startswith("gsk")
        assert formatos["gemini"].startswith("AIza")

    def test_hay_al_menos_tres_proveedores_gratis_con_consola(self, servicio):
        gratis = [p for p in servicio.catalogo() if p["gratis"]]
        assert len(gratis) >= 3
        assert all(p["consola"] for p in gratis)


class TestClavePorProveedor:
    """El dueño del deploy puede cargar las de free tier sin arriesgar nada."""

    def _con_claves(self, claves):
        return _servicio(llm_api_keys=claves)

    def test_la_clave_del_proveedor_gana_sobre_la_generica(self):
        svc = self._con_claves({"groq": "gsk_propia"})
        assert svc.manager.clave_de("groq") == "gsk_propia"

    def test_sin_clave_propia_cae_a_la_generica(self):
        assert _servicio(llm_api_key="generica").manager.clave_de("groq") == "generica"

    def test_anthropic_usa_su_variable_de_siempre(self, servicio):
        assert servicio.manager.clave_de("anthropic") == "clave-del-servidor"

    def test_cada_paso_recibe_la_clave_de_SU_proveedor(self):
        """Con dos proveedores distintos, una sola clave no alcanza."""
        svc = self._con_claves({"groq": "gsk_x", "gemini": "AIza_y"})
        svc.guardar(PASO_POLITICAS, "groq", "llama")
        svc.guardar(PASO_JUEZ, "gemini", "gemini-2.5-flash")
        assert svc.cliente(PASO_POLITICAS).api_key == "gsk_x"
        assert svc.cliente(PASO_JUEZ).api_key == "AIza_y"

    def test_el_catalogo_marca_cual_tiene_clave(self):
        catalogo = {p["id"]: p for p in self._con_claves({"groq": "gsk_x"}).catalogo()}
        assert catalogo["groq"]["tiene_clave"] is True
        assert catalogo["gemini"]["tiene_clave"] is False


class TestLaEleccionDeLaSesion:
    """Un visitante puede probar otro modelo sin cambiárselo a los demás.

    En una instancia pública es la única forma sensata: que la elección de uno
    cambie lo que ve el próximo sería una sorpresa; que no pueda probar otro
    modelo sería inútil.
    """

    OVERRIDE = {"judge": {"proveedor": "groq", "modelo": "llama-3.3-70b-versatile"}}

    def test_el_override_pisa_solo_ese_paso(self, servicio):
        config = servicio.con_override(self.OVERRIDE)
        assert config[PASO_JUEZ]["modelo"] == "llama-3.3-70b-versatile"
        assert config[PASO_POLITICAS]["modelo"] == "claude-haiku-4-5-20251001"

    def test_el_override_no_toca_lo_guardado(self, servicio):
        servicio.con_override(self.OVERRIDE)
        assert servicio.db.get_modelos() == {}
        assert servicio.vigente()[PASO_JUEZ]["modelo"] == "claude-sonnet-4-6"

    def test_queda_marcado_como_de_la_sesion(self, servicio):
        config = servicio.con_override(self.OVERRIDE)
        assert config[PASO_JUEZ]["de_la_sesion"] is True
        assert "de_la_sesion" not in config[PASO_POLITICAS]

    def test_sin_override_devuelve_lo_vigente(self, servicio):
        assert servicio.con_override(None) == servicio.vigente()

    def test_un_paso_inventado_se_ignora_en_vez_de_romper(self, servicio):
        config = servicio.con_override({"inventado": {"proveedor": "groq", "modelo": "x"}})
        assert set(config) == set(PASOS_DEL_PIPELINE)

    def test_un_modelo_vacio_conserva_el_vigente(self, servicio):
        config = servicio.con_override({"judge": {"proveedor": "groq", "modelo": "   "}})
        assert config[PASO_JUEZ]["modelo"] == "claude-sonnet-4-6"

    def test_los_clientes_de_la_sesion_no_se_cachean(self, servicio):
        a = servicio.clientes_para(self.OVERRIDE)
        b = servicio.clientes_para(self.OVERRIDE)
        assert a[PASO_JUEZ] is not b[PASO_JUEZ]

    def test_devuelve_un_cliente_por_paso(self, servicio):
        assert set(servicio.clientes_para(self.OVERRIDE)) == set(PASOS_DEL_PIPELINE)


class TestTarifas:
    """El costo que informa el panel tiene que ser el real.

    Sin entradas propias, `gpt-4o-mini` caia en la tarifa de referencia —la de
    Sonnet— y el panel informaba veinte veces mas de lo que costaba.
    """

    @pytest.mark.parametrize("modelo,esperado", [
        ("gpt-4o-mini", 0.15),
        ("gpt-4o-mini-2024-07-18", 0.15),
        ("gpt-4o", 2.50),
        ("gpt-4o-2024-11-20", 2.50),
        ("claude-haiku-4-5-20251001", 0.80),
        ("claude-sonnet-4-6", 3.00),
    ])
    def test_cada_modelo_cotiza_con_su_tarifa(self, modelo, esperado):
        from api.app.llm.pricing import estimar_costo_usd

        assert estimar_costo_usd(modelo, 1_000_000, 0) == pytest.approx(esperado)

    def test_el_mini_no_se_cotiza_como_el_grande(self):
        """`gpt-4o` es subcadena de `gpt-4o-mini`: el orden del diccionario decide."""
        from api.app.llm.pricing import estimar_costo_usd

        mini = estimar_costo_usd("gpt-4o-mini", 1_000_000, 0)
        grande = estimar_costo_usd("gpt-4o", 1_000_000, 0)
        assert mini < grande, "el mini se esta cotizando con la tarifa del grande"


class TestLosDosModos:
    """Produccion es Claude con la clave del visitante; demo, el modelo del servidor.

    Son dos decisiones distintas y viven en dos lugares distintos. Configurar
    `demo_provider` ES la decision de que el modo demo corra: sin eso, sirve los
    informes guardados, que es el comportamiento seguro.
    """

    def _svc(self, **extra):
        return _servicio(**extra)

    def test_sin_configurar_el_modo_demo_no_corre(self):
        assert self._svc().modelo_demo() is None

    def test_configurado_y_con_clave_corre(self):
        svc = self._svc(demo_provider="groq", demo_model="llama-3.3-70b-versatile",
                        llm_api_keys={"groq": "gsk_x"})
        assert svc.modelo_demo() == {"proveedor": "groq", "modelo": "llama-3.3-70b-versatile"}

    def test_configurado_sin_clave_no_corre(self):
        """Sin credencial no hay nada que ejecutar: se cae al informe guardado."""
        svc = self._svc(demo_provider="groq", demo_model="llama", llm_api_keys={}, llm_api_key="")
        assert svc.modelo_demo() is None

    def test_sin_modelo_no_alcanza_con_el_proveedor(self):
        svc = self._svc(demo_provider="groq", demo_model="", llm_api_keys={"groq": "x"})
        assert svc.modelo_demo() is None

    def test_los_tres_pasos_corren_en_el_modelo_del_demo(self):
        svc = self._svc(demo_provider="openai", demo_model="gpt-4o-mini",
                        llm_api_keys={"openai": "sk-x"})
        config = svc.config_demo()
        assert {c["modelo"] for c in config.values()} == {"gpt-4o-mini"}
        assert all(c["es_demo"] for c in config.values())

    def test_el_modo_demo_no_toca_la_configuracion_de_produccion(self):
        """Claude sigue estando: demo es otra cosa, no un reemplazo."""
        svc = self._svc(demo_provider="openai", demo_model="gpt-4o-mini",
                        llm_api_keys={"openai": "sk-x"})
        svc.config_demo()
        assert svc.vigente()[PASO_POLITICAS]["proveedor"] == "anthropic"
        assert svc.vigente()[PASO_RESOLUCION]["modelo"] == "claude-sonnet-4-6"

    def test_los_clientes_del_demo_usan_la_clave_del_servidor(self):
        svc = self._svc(demo_provider="groq", demo_model="llama", llm_api_keys={"groq": "gsk_x"})
        clientes = svc.clientes_demo()
        assert all(c.api_key == "gsk_x" for c in clientes.values())

    def test_sin_modo_demo_configurado_no_hay_clientes(self):
        assert self._svc().clientes_demo() is None


class TestLLMManager:
    """El componente que habla con los proveedores.

    Sabe resolver credenciales, construir el cliente que corresponda y cerrarlo.
    No sabe que existe SQLite ni que el pipeline tiene tres pasos — de eso se
    ocupa `ModelosService`. La division importa: hubo un momento en que tres
    lugares distintos ensamblaban clientes y alcanzo con que uno quedara
    desfasado para que el panel dijera una cosa y n8n hiciera otra.
    """

    @staticmethod
    def _manager(monkeypatch, **extra):
        from api.app.llm.manager import LLMManager

        m = LLMManager(settings_falsas(**extra), MagicMock())
        creados = []

        def falso(proveedor, modelo, api_key):
            c = MagicMock(proveedor=proveedor, modelo=modelo, api_key=api_key)
            creados.append(c)
            return c

        monkeypatch.setattr(m, "_construir", falso)
        m.creados = creados
        return m

    def test_el_cliente_del_servidor_se_cachea(self, monkeypatch):
        m = self._manager(monkeypatch)
        assert m.cliente("groq", "llama") is m.cliente("groq", "llama")
        assert len(m.creados) == 1

    def test_modelos_distintos_no_comparten_cliente(self, monkeypatch):
        m = self._manager(monkeypatch)
        assert m.cliente("groq", "llama") is not m.cliente("groq", "qwen")

    def test_el_cliente_con_clave_propia_nunca_se_cachea(self, monkeypatch):
        """La credencial es de quien hizo la peticion, no del proceso."""
        m = self._manager(monkeypatch)
        a = m.cliente("groq", "llama", api_key="sk-visitante")
        b = m.cliente("groq", "llama", api_key="sk-visitante")
        assert a is not b

    def test_invalidar_cierra_lo_cacheado(self, monkeypatch):
        m = self._manager(monkeypatch)
        cliente = m.cliente("groq", "llama")
        m.invalidar()
        assert cliente.close.called
        assert m.cliente("groq", "llama") is not cliente

    def test_cerrar_todos_no_repite(self, monkeypatch):
        """Un mismo cliente en dos pasos se cierra una sola vez."""
        m = self._manager(monkeypatch)
        uno = m.cliente("groq", "llama", api_key="sk-visitante")
        m.cerrar_todos({"a": uno, "b": uno})
        assert uno.close.call_count == 1

    def test_anthropic_va_por_su_sdk_y_el_resto_por_http(self):
        """Sin monkeypatch: se comprueba que clase construye de verdad."""
        from api.app.llm.client import AnthropicClient, OpenAICompatibleClient
        from api.app.llm.manager import LLMManager

        m = LLMManager(settings_falsas(llm_api_keys={"groq": "gsk_x"}), MagicMock())
        assert isinstance(m.cliente("anthropic", "claude-haiku-4-5-20251001"), AnthropicClient)
        assert isinstance(m.cliente("groq", "llama-3.3-70b-versatile"), OpenAICompatibleClient)
        m.invalidar()

    def test_no_cierra_los_clientes_compartidos(self, monkeypatch):
        """Regresion: la primera peticion andaba y la segunda moria.

        Un cliente sin clave propia sale del cache y lo reusa la proxima
        peticion. El pipeline efimero los cerraba a todos al terminar, asi que
        el siguiente encontraba el pool muerto: «Cannot send a request, as the
        client has been closed». No se veia desde el panel porque ahi cada
        visitante trae su clave —esos si son efimeros— y aparecio recien cuando
        el modo demo empezo a correr con la del servidor.
        """
        m = self._manager(monkeypatch)
        compartido = m.cliente("gemini", "flash")
        m.cerrar_todos({"a": compartido})
        assert not compartido.close.called, "se cerro un cliente que van a reusar"
        assert m.cliente("gemini", "flash") is compartido

    def test_si_cierra_los_de_la_peticion(self, monkeypatch):
        m = self._manager(monkeypatch)
        propio = m.cliente("gemini", "flash", api_key="sk-visitante")
        m.cerrar_todos({"a": propio})
        assert propio.close.called

    def test_una_mezcla_cierra_solo_lo_que_corresponde(self, monkeypatch):
        m = self._manager(monkeypatch)
        compartido = m.cliente("gemini", "flash")
        propio = m.cliente("groq", "llama", api_key="sk-visitante")
        m.cerrar_todos({"a": compartido, "b": propio})
        assert not compartido.close.called
        assert propio.close.called
