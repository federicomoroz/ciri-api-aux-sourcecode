"""Agregar un proveedor tiene que ser una sola edicion, y fallar fuerte si no.

Estaba en tres tablas paralelas con la misma clave: la URL base en `client.py`,
el adaptador en `adaptadores.py`, los metadatos del panel en `constants.py`. Las
tres estaban sincronizadas —lo verifique antes de unirlas—, pero nada lo
garantizaba, y **el modo de falla de olvidarse una era silencioso**: sin URL base,
`_construir` caia al cliente de Anthropic y mandaba el modelo de Groq con la
credencial de Groq a la API de Anthropic. El error hablaba de autenticacion.
"""

from types import SimpleNamespace

import pytest

from api.app.llm.adaptadores import ABIERTO, adaptador_de
from api.app.llm.manager import LLMManager
from api.app.llm.proveedores import (
    ADAPTADOR_POR_PROVEEDOR,
    BASES_OPENAI,
    PROVEEDOR_ANTHROPIC,
    PROVEEDORES,
    base_url_de,
    catalogo,
    existe,
)
from api.app.observability.tracer import NoOpTracer

CAMPOS = ("base_url", "adaptador", "nombre", "gratis", "modelos", "consola", "formato_clave")


@pytest.fixture
def manager():
    ajustes = SimpleNamespace(
        llm_base_url="", anthropic_api_key="sk-ant-x", llm_api_key="",
        llm_api_keys={}, llm_max_retries=2, llm_rpm={},
    )
    return LLMManager(ajustes, NoOpTracer())


class TestCadaProveedorEstaCompleto:
    """Una entrada a medias es la que reproduce el defecto que esto vino a cerrar."""

    @pytest.mark.parametrize("pid", sorted(PROVEEDORES))
    def test_declara_todos_los_campos(self, pid):
        faltan = [c for c in CAMPOS if c not in PROVEEDORES[pid]]
        assert not faltan, f"{pid} no declara {faltan}"

    @pytest.mark.parametrize("pid", sorted(PROVEEDORES))
    def test_tiene_a_donde_hablarle(self, pid):
        """Vacio solo lo puede tener Anthropic, que usa su propio SDK."""
        if not PROVEEDORES[pid]["base_url"]:
            assert pid == PROVEEDOR_ANTHROPIC, (
                f"{pid} no tiene URL base y no es el del SDK propio: "
                f"`_construir` lo mandaria a la API de Anthropic"
            )

    @pytest.mark.parametrize("pid", sorted(PROVEEDORES))
    def test_sugiere_al_menos_un_modelo(self, pid):
        assert PROVEEDORES[pid]["modelos"], f"{pid} no sugiere ninguno: el panel lo muestra vacio"

    @pytest.mark.parametrize("pid", sorted(PROVEEDORES))
    def test_dice_de_donde_sacar_la_clave(self, pid):
        """Sin esto, elegir un proveedor deja al que lo eligio buscando en Google."""
        assert PROVEEDORES[pid]["consola"].startswith("http")


class TestLasVistasDerivanDelRegistro:
    """Las tres tablas viejas ahora se calculan. No pueden desincronizarse."""

    def test_las_bases_openai_son_todos_menos_el_del_sdk_propio(self):
        assert set(BASES_OPENAI) == set(PROVEEDORES) - {PROVEEDOR_ANTHROPIC}

    def test_cada_proveedor_tiene_su_adaptador(self):
        assert set(ADAPTADOR_POR_PROVEEDOR) == set(PROVEEDORES)

    def test_el_catalogo_del_panel_no_expone_el_adaptador(self):
        """Es una decision interna de como se le habla, no algo que se elija."""
        assert all("adaptador" not in e for e in catalogo())

    def test_el_catalogo_los_lista_a_todos(self):
        assert {e["id"] for e in catalogo()} == set(PROVEEDORES)


class TestUnProveedorDesconocidoNoSeDisimula:

    def test_existe_distingue(self):
        assert existe("groq") and not existe("no-existe")

    def test_construir_lanza_en_vez_de_caer_a_anthropic(self, manager):
        """El defecto original: terminaba en un error de autenticacion de Anthropic."""
        with pytest.raises(ValueError, match="proveedor desconocido"):
            manager._construir("no-existe", "algun-modelo", "")

    def test_el_mensaje_dice_donde_agregarlo(self, manager):
        with pytest.raises(ValueError, match="proveedores.py"):
            manager._construir("no-existe", "algun-modelo", "")

    def test_una_url_explicita_alcanza_para_uno_no_registrado(self, manager):
        """La lista es comodidad, no restriccion: con URL se puede hablar igual."""
        manager.settings.llm_base_url = "http://mi-endpoint/v1"
        cliente = manager._construir("no-registrado", "modelo-x", "")
        assert type(cliente).__name__ == "OpenAICompatibleClient"

    def test_los_conocidos_se_construyen(self, manager):
        assert type(manager._construir(PROVEEDOR_ANTHROPIC, "claude-x", "")).__name__ == "AnthropicClient"
        assert type(manager._construir("groq", "llama-x", "")).__name__ == "OpenAICompatibleClient"


class TestElAdaptadorSaleDelRegistro:

    def test_uno_conocido_usa_el_suyo(self):
        assert adaptador_de("gemini") is ADAPTADOR_POR_PROVEEDOR["gemini"]

    def test_uno_desconocido_cae_al_mas_tolerante(self):
        """Equivocarse hacia un techo alto cuesta latencia; hacia uno bajo corta."""
        assert adaptador_de("no-existe") is ABIERTO

    def test_el_modelo_le_gana_al_proveedor(self):
        """OpenRouter sirve Llama y Gemini, y no se les habla igual."""
        assert adaptador_de("openrouter", "google/gemini-flash") is not adaptador_de("openrouter", "llama-3")


def test_la_url_explicita_le_gana_a_la_del_registro():
    assert base_url_de("groq", "http://otra/v1") == "http://otra/v1"
