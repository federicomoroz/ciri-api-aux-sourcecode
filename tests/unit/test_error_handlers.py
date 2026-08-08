"""Los errores de los proveedores externos llegan explicados, no como un 500 mudo.

Quien prueba el sistema no tiene acceso a los logs del servidor. Si el saldo de
Anthropic se agota o Voyage corta por limite de rate, la respuesta tiene que
decir que paso y, cuando exista, cual es la salida.

Lo que se prueba es el comportamiento del borde HTTP. La clasificacion en si
—que un mensaje sea «sin saldo» y no otra cosa— se prueba aparte, en
`test_fallos.py`, porque la usan tres caminos y no solo este.
"""

import anthropic
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app.main import manejar_error
from api.app.rag.embedder import EmbeddingRateLimit

SIN_SALDO = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
)
OTRO_ERROR = "Error code: 400 - {'error': {'message': 'max_tokens: must be >= 1'}}"

RUTA = "/estalla"


def _cliente(excepcion: Exception) -> TestClient:
    """Una app minima cuya unica ruta lanza esa excepcion."""
    app = FastAPI()
    # Un solo manejador para todo: clasifica con `domain/fallos.py` y traduce.
    # Antes eran dos handlers con su propia clasificacion y su propia redaccion.
    app.add_exception_handler(Exception, manejar_error)

    @app.get(RUTA)
    def estallar():
        raise excepcion

    return TestClient(app, raise_server_exceptions=False)


def _error_anthropic(mensaje: str) -> anthropic.APIStatusError:
    peticion = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    respuesta = httpx.Response(400, request=peticion)
    return anthropic.BadRequestError(mensaje, response=respuesta, body=None)


class TestSaldoAgotado:
    @pytest.fixture
    def cuerpo(self) -> dict:
        return _cliente(_error_anthropic(SIN_SALDO)).get(RUTA).json()

    def test_dice_que_el_problema_es_el_saldo(self, cuerpo):
        assert "sin saldo" in cuerpo["error"].lower()

    def test_ofrece_la_salida_que_el_evaluador_puede_tomar(self, cuerpo):
        """Es el unico fallo de proveedor que quien prueba puede resolver solo."""
        assert "api_key" in cuerpo["detail"]

    def test_aclara_que_el_resto_del_sistema_sigue_en_pie(self, cuerpo):
        assert "RAG" in cuerpo["detail"]

    def test_no_filtra_el_mensaje_crudo_del_proveedor(self, cuerpo):
        assert "invalid_request_error" not in str(cuerpo)

    def test_conserva_el_request_id_para_correlacionar(self, cuerpo):
        assert cuerpo["request_id"]


class TestOtrosErroresDelModelo:
    @pytest.fixture
    def cuerpo(self) -> dict:
        return _cliente(_error_anthropic(OTRO_ERROR)).get(RUTA).json()

    def test_no_se_confunde_con_falta_de_saldo(self, cuerpo):
        """Sugerir una clave propia por un error de parametros seria enganoso."""
        assert "sin saldo" not in cuerpo["error"].lower()
        assert "api_key" not in cuerpo["detail"]

    def test_nombra_el_tipo_de_error(self, cuerpo):
        assert "BadRequestError" in cuerpo["error"]


class TestLimiteDeEmbeddings:
    @pytest.fixture
    def respuesta(self):
        return _cliente(EmbeddingRateLimit("3 RPM")).get(RUTA)

    def test_responde_429_y_no_500(self, respuesta):
        """429 es reintentable; 500 le dice a n8n que el sistema esta roto."""
        assert respuesta.status_code == 429

    def test_explica_que_quedo_sin_servicio(self, respuesta):
        """Nombra las dos capacidades que dependen de los embeddings.

        Sin eso, «límite de frecuencia» no le dice a nadie qué dejó de andar.
        """
        detalle = respuesta.json()["detail"]
        assert "políticas" in detalle and "casos similares" in detalle

    def test_dice_que_se_puede_reintentar(self, respuesta):
        assert "Reintentar" in respuesta.json()["detail"]
