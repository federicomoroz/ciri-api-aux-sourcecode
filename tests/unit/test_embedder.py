"""El embedder cachea y distingue el limite de rate.

El free tier de Voyage permite 3 peticiones por minuto. Sin cache, investigar
el mismo caso dos veces gastaba dos de esas tres, y el limite llegaba al
evaluador como un 500 sin explicacion.
"""

import numpy as np
import pytest

from api.app.rag.embedder import EmbeddingRateLimit, FastEmbedder
from api.app.rate_limiter import RateLimiter

MODULO = "api.app.rag.embedder"
TOPE_DE_CACHE = f"{MODULO}.EMBEDDING_CACHE_MAX"
ESPERA = f"{MODULO}.EMBEDDING_RATE_LIMIT_WAIT_S"
REINTENTOS = f"{MODULO}.EMBEDDING_RATE_LIMIT_RETRIES"

MODELO = "voyage-multilingual-2"
DIMS = 4

CONSULTA = "contracargo cripto score 8"
OTRA_CONSULTA = "contracargo tarjeta score 4"

MENSAJE_DEL_LIMITE = "reduced rate limits of 3 RPM"
MENSAJE_DE_OTRO_ERROR = "modelo inexistente"

# La excepcion REAL del SDK, no un doble con el mismo nombre. El embedder la
# atrapa por tipo, asi que un doble ya no serviria — y esa es justamente la
# propiedad que interesa fijar: mientras el reconocimiento era por
# `type(e).__name__`, este test pasaba con una clase inventada y habria seguido
# pasando si Voyage renombraba la suya. Verificaba el string, no el contrato.
from voyageai.error import RateLimitError  # noqa: E402


@pytest.fixture(autouse=True)
def sin_espera(monkeypatch):
    """Los tests no esperan los segundos reales entre reintentos."""
    monkeypatch.setattr(ESPERA, 0)


class ClienteFalso:
    """Cuenta las llamadas y registra que textos se pidieron."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.llamadas = 0
        self.textos_pedidos: list[list[str]] = []

    def embed(self, texts, model):
        self.llamadas += 1
        self.textos_pedidos.append(list(texts))
        if self.error:
            raise self.error
        # Vector determinista por texto, para poder comprobar que cada uno
        # recibe el suyo y no el del vecino.
        return type("R", (), {"embeddings": [[float(len(t))] * DIMS for t in texts]})()


class ClienteQueSeLibera(ClienteFalso):
    """Falla las primeras N veces y despues responde, como un limite que cede."""

    def __init__(self, fallos: int, **kw):
        super().__init__(**kw)
        self.fallos = fallos

    def embed(self, texts, model):
        if self.llamadas < self.fallos:
            self.llamadas += 1
            raise RateLimitError(MENSAJE_DEL_LIMITE)
        return super().embed(texts, model)


def _limitador_sin_reloj() -> RateLimiter:
    """Reparte turnos con un reloj de mentira: cuenta igual, pero no duerme.

    El limite real de Voyage son 3 por minuto, y varios tests hacen mas de tres
    llamadas a proposito —el del cache acotado hace decenas—. Con el reloj de
    verdad, ese test solo tardaba sesenta segundos esperando una cuota que en un
    test no existe.
    """
    ahora = {"t": 0.0}

    def dormir(s: float) -> None:
        ahora["t"] += s      # pasa el tiempo sin que pase el tiempo

    return RateLimiter(reloj=lambda: ahora["t"], dormir=dormir)


def _embedder(cliente) -> FastEmbedder:
    e = FastEmbedder(model_name=MODELO, api_key="test", limitador=_limitador_sin_reloj())
    e._client = cliente
    return e


class TestCache:
    def test_el_mismo_texto_no_se_pide_dos_veces(self):
        cliente = ClienteFalso()
        e = _embedder(cliente)
        e.encode([CONSULTA])
        e.encode([CONSULTA])
        assert cliente.llamadas == 1

    def test_devuelve_el_mismo_vector_desde_cache(self):
        e = _embedder(ClienteFalso())
        assert np.array_equal(e.encode([CONSULTA]), e.encode([CONSULTA]))

    def test_solo_pide_los_textos_que_faltan(self):
        cliente = ClienteFalso()
        e = _embedder(cliente)
        e.encode([CONSULTA])
        e.encode([CONSULTA, OTRA_CONSULTA])
        assert cliente.textos_pedidos == [[CONSULTA], [OTRA_CONSULTA]]

    def test_cada_texto_recibe_su_propio_vector(self):
        """Mezclar cacheados con nuevos no debe desordenar la salida."""
        cortos = ["a", "bb", "ccc"]
        e = _embedder(ClienteFalso())
        e.encode([cortos[1]])
        salida = e.encode(cortos)
        assert [v[0] for v in salida] == [float(len(t)) for t in cortos]

    def test_no_pide_duplicados_dentro_de_la_misma_llamada(self):
        cliente = ClienteFalso()
        _embedder(cliente).encode([CONSULTA, CONSULTA, OTRA_CONSULTA])
        assert cliente.textos_pedidos == [[CONSULTA, OTRA_CONSULTA]]

    def test_conserva_el_orden_y_los_repetidos_de_la_peticion(self):
        e = _embedder(ClienteFalso())
        salida = e.encode([CONSULTA, OTRA_CONSULTA, CONSULTA])
        assert salida.shape == (3, DIMS)
        assert np.array_equal(salida[0], salida[2])

    def test_lista_vacia_no_llama_al_proveedor(self):
        cliente = ClienteFalso()
        assert _embedder(cliente).encode([]).shape == (0, 0)
        assert cliente.llamadas == 0

    def test_el_cache_esta_acotado(self, monkeypatch):
        """Un proceso largo no deberia crecer sin limite."""
        tope = 3
        monkeypatch.setattr(TOPE_DE_CACHE, tope)
        e = _embedder(ClienteFalso())
        for i in range(tope * 2):
            e.encode([f"{CONSULTA} {i}"])
        assert len(e._cache) <= tope


class TestLimiteDeRate:
    def test_el_limite_se_distingue_del_resto_de_los_errores(self):
        e = _embedder(ClienteFalso(error=RateLimitError(MENSAJE_DEL_LIMITE)))
        with pytest.raises(EmbeddingRateLimit):
            e.encode([CONSULTA])

    def test_conserva_el_mensaje_del_proveedor(self):
        e = _embedder(ClienteFalso(error=RateLimitError(MENSAJE_DEL_LIMITE)))
        with pytest.raises(EmbeddingRateLimit, match=MENSAJE_DEL_LIMITE):
            e.encode([CONSULTA])

    def test_los_demas_errores_siguen_saliendo_tal_cual(self):
        e = _embedder(ClienteFalso(error=ValueError(MENSAJE_DE_OTRO_ERROR)))
        with pytest.raises(ValueError):
            e.encode([CONSULTA])

    def test_los_demas_errores_no_se_reintentan(self):
        """Reintentar un modelo inexistente solo suma latencia."""
        cliente = ClienteFalso(error=ValueError(MENSAJE_DE_OTRO_ERROR))
        with pytest.raises(ValueError):
            _embedder(cliente).encode([CONSULTA])
        assert cliente.llamadas == 1

    def test_un_fallo_no_deja_basura_en_el_cache(self):
        e = _embedder(ClienteFalso(error=RateLimitError(MENSAJE_DEL_LIMITE)))
        with pytest.raises(EmbeddingRateLimit):
            e.encode([CONSULTA])
        assert e._cache == {}


class TestReintento:
    def test_un_limite_pasajero_termina_en_exito(self):
        e = _embedder(ClienteQueSeLibera(fallos=1))
        assert e.encode([CONSULTA]).shape == (1, DIMS)

    def test_reintenta_las_veces_configuradas_y_no_mas(self, monkeypatch):
        """Con 3 RPM, insistir de mas solo agrega latencia al informe."""
        reintentos = 1
        monkeypatch.setattr(REINTENTOS, reintentos)
        cliente = ClienteQueSeLibera(fallos=99)
        with pytest.raises(EmbeddingRateLimit):
            _embedder(cliente).encode([CONSULTA])
        assert cliente.llamadas == reintentos + 1

    def test_lo_recuperado_tras_reintentar_queda_cacheado(self):
        cliente = ClienteQueSeLibera(fallos=1)
        e = _embedder(cliente)
        e.encode([CONSULTA])
        antes = cliente.llamadas
        e.encode([CONSULTA])
        assert cliente.llamadas == antes
