"""Clasificar un fallo tiene que ser mas robusto que buscar un substring.

Este modulo existe porque la clasificacion estaba en tres lugares y las tres
veces era `MARKER in str(exc).lower()`. Esa tecnica ya fallo en produccion: el
workflow de n8n tenia un `.includes('404')` y un 503 lo disparaba, porque el
mensaje traia adentro la pagina de arranque de Render —263 KB de HTML con «404»
en el nombre de un archivo de fuente—. TXN-00051, que existe, salia como «la
transaccion no existe en la base».

El orden de confianza que se prueba aca es: tipo de excepcion, despues codigo de
estado, y **solo al final** substring — y solo donde el SDK no da otra cosa.
"""

import pytest

from api.app.domain.fallos import RESPUESTAS, Fallo, clasificar, respuesta_de
from api.app.rag.embedder import EmbeddingRateLimit


class ConCodigo(Exception):
    """Una excepcion que trae su codigo, como las de los SDK."""

    def __init__(self, mensaje: str, codigo: int):
        super().__init__(mensaje)
        self.status_code = codigo


class ConRespuesta(Exception):
    """Otras lo traen adentro de `.response`, como httpx."""

    def __init__(self, mensaje: str, codigo: int):
        super().__init__(mensaje)
        self.response = type("R", (), {"status_code": codigo})()


class TestNoConfundirUnCodigoConSuAparicionEnElTexto:
    """El defecto que este modulo vino a cerrar, fijado como test.

    Un mensaje que CONTIENE «404» no es un 404. La pagina de arranque de Render
    es el caso real: 503 de estado y «404» en el cuerpo.
    """

    ARRANQUE_DE_RENDER = (
        '503 - "<!doctype html><html><head><style>@font-face{'
        'src:url(/fonts/roobert-404.woff2)}</style></head>'
        "<body>Application loading</body></html>\""
    )

    def test_un_503_con_404_en_el_cuerpo_no_es_dato_inexistente(self):
        assert clasificar(ConCodigo(self.ARRANQUE_DE_RENDER, 503)) is not Fallo.DATO_INEXISTENTE

    def test_y_tampoco_se_clasifica_como_otra_cosa(self):
        """Sin nombre es la respuesta correcta: un 500 honesto vale mas que un invento."""
        assert clasificar(ConCodigo(self.ARRANQUE_DE_RENDER, 503)) is None

    def test_un_404_de_verdad_si_se_reconoce(self):
        assert clasificar(ConCodigo("not found", 404)) is Fallo.DATO_INEXISTENTE


class TestElOrdenDeConfianza:

    def test_el_tipo_manda_sobre_todo(self):
        """`EmbeddingRateLimit` es del proyecto: no hay que adivinarlo del texto."""
        assert clasificar(EmbeddingRateLimit("3 RPM")) is Fallo.CUOTA_DE_EMBEDDINGS

    def test_el_codigo_manda_sobre_el_texto(self):
        assert clasificar(ConCodigo("lo que sea", 401)) is Fallo.CLAVE_INVALIDA

    def test_el_codigo_se_lee_tambien_desde_la_respuesta(self):
        assert clasificar(ConRespuesta("lo que sea", 401)) is Fallo.CLAVE_INVALIDA

    def test_el_substring_es_el_ultimo_recurso(self):
        """Anthropic manda «sin saldo» con un 400, indistinguible por codigo."""
        exc = ConCodigo("Your credit balance is too low to access the API", 400)
        assert clasificar(exc) is Fallo.SIN_SALDO

    def test_un_400_sin_marcador_no_se_inventa(self):
        assert clasificar(ConCodigo("max_tokens: must be >= 1", 400)) is None


class TestProveedorDesconocido:
    """Lo lanza `LLMManager._construir` cuando el id no esta en el registro."""

    def test_se_reconoce(self):
        assert clasificar(ValueError("proveedor desconocido: 'x'")) is Fallo.PROVEEDOR_DESCONOCIDO

    def test_otro_ValueError_no(self):
        assert clasificar(ValueError("algo distinto")) is None


class TestCadaFalloTieneQueDecirAlgoUtil:
    """Un catalogo de errores que no dice que hacer es un catalogo de excusas."""

    @pytest.mark.parametrize("fallo", list(Fallo))
    def test_esta_en_la_tabla(self, fallo):
        assert fallo in RESPUESTAS, f"{fallo} se puede clasificar pero no se sabe que responder"

    @pytest.mark.parametrize("fallo", list(Fallo))
    def test_el_estado_es_de_cliente_o_de_cuota(self, fallo):
        """Ninguno de estos es un 500: son condiciones previstas, no fallas nuestras."""
        assert 400 <= RESPUESTAS[fallo].status < 500

    @pytest.mark.parametrize("fallo", list(Fallo))
    def test_el_detalle_explica_y_no_solo_nombra(self, fallo):
        assert len(RESPUESTAS[fallo].detalle) > 60

    @pytest.mark.parametrize("fallo", list(Fallo))
    def test_el_titular_y_la_explicacion_viajan_juntos_cuando_hace_falta(self, fallo):
        r = RESPUESTAS[fallo]
        assert r.titulo in r.para_leer and r.detalle in r.para_leer

    def test_quedarse_sin_saldo_deja_constancia(self):
        """Es operativo: alguien tiene que enterarse de que el deploy dejo de andar."""
        assert RESPUESTAS[Fallo.SIN_SALDO].alerta is True

    def test_una_clave_mal_pegada_no(self):
        """Es la clave de quien la pego, y ya se lo dijimos en la respuesta."""
        assert RESPUESTAS[Fallo.CLAVE_INVALIDA].alerta is False


class TestElAtajo:

    def test_devuelve_la_respuesta_de_lo_clasificado(self):
        assert respuesta_de(EmbeddingRateLimit("x")) is RESPUESTAS[Fallo.CUOTA_DE_EMBEDDINGS]

    def test_y_nada_cuando_no_hay_nombre(self):
        assert respuesta_de(RuntimeError("boom")) is None
