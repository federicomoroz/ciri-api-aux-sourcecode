"""
Voyage AI embedder.

voyage-multilingual-2: 1024 dims, native Spanish/multilingual, no local model.
Thread-safe via double-checked locking.

Dos cosas que no son obvias y que el free tier vuelve importantes:

1. Cachea. El QueryBuilder es deterministico, asi que investigar dos veces el
   mismo caso produce exactamente el mismo texto de consulta. Sin cache, cada
   repeticion gastaba una peticion de un presupuesto de 3 por minuto.
2. El limite de rate se distingue del resto de los errores, para que la API
   pueda responder 429 con una explicacion en vez de un 500 mudo.
"""

import inspect
import logging
import os
import threading
import time

import numpy as np

from ..domain.constants import (
    EMBEDDING_CACHE_MAX,
    EMBEDDING_RATE_LIMIT_RETRIES,
    EMBEDDING_RATE_LIMIT_WAIT_S,
    EMBEDDING_RPM,
    EMBEDDING_TIMEOUT_S,
    EMBEDDING_VENTANA_S,
    PROVEEDOR_EMBEDDINGS,
)
from ..rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


def _opciones_del_cliente(voyageai, key: str) -> dict:
    """Los argumentos del cliente de Voyage, con timeout si esta version lo acepta.

    Sin timeout, una llamada colgada cuelga el indexado del arranque y la API
    nunca llega a responder /health: en Render eso es un deploy fallido sin
    diagnostico. El pin es `voyageai>=0.2.3` y el nombre del parametro no es el
    mismo en toda esa franja, asi que se pregunta antes de pasarlo — mandar un
    kwarg que no existe seria cambiar un cuelgue por un TypeError al arrancar.
    """
    opciones = {"api_key": key}
    if "timeout" in inspect.signature(voyageai.Client).parameters:
        opciones["timeout"] = EMBEDDING_TIMEOUT_S
    else:
        logger.warning(
            "El cliente de Voyage instalado no acepta timeout: una llamada colgada "
            "no se corta sola"
        )
    return opciones


class EmbeddingRateLimit(RuntimeError):
    """El proveedor de embeddings rechazo la peticion por limite de rate."""


class FastEmbedder:
    """Voyage AI embedder con cache en proceso."""

    def __init__(
        self, model_name: str, api_key: str = "", limitador: RateLimiter | None = None,
    ) -> None:
        self._model_name = model_name
        self._api_key = api_key
        self._client = None
        self._lock = threading.Lock()
        self._cache: dict[str, np.ndarray] = {}
        # El free tier de Voyage es el limite mas ajustado del sistema: 3 por
        # minuto. Hasta que esto existio, el reparto de turnos solo lo tenia el
        # camino del modelo, y una tanda de analisis seguidos comia un 429 —
        # medido contra el deploy.
        self._limitador = limitador or RateLimiter(ventana_s=EMBEDDING_VENTANA_S)

    def _ensure_loaded(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import voyageai
                    key = self._api_key or os.environ.get("CB_VOYAGE_API_KEY", "")
                    if not key:
                        raise RuntimeError(
                            "CB_VOYAGE_API_KEY is required. "
                            "Get a free key at https://dash.voyageai.com/"
                        )
                    self._client = voyageai.Client(**_opciones_del_cliente(voyageai, key))
                    logger.info("Voyage AI client initialized with model=%s", self._model_name)
        return self._client

    def encode(self, texts: list[str]) -> np.ndarray:
        """Vectores de esos textos. Solo salen a la API los que no estan en cache."""
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        pendientes = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if pendientes:
            self._cachear(self._pedir(pendientes), pendientes)
        else:
            logger.debug("Embeddings servidos desde cache: %d textos", len(texts))

        return np.array([self._cache[t] for t in texts], dtype=np.float32)

    def _pedir(self, textos: list[str]) -> list[list[float]]:
        """Una peticion al proveedor, esperando turno para no toparse el limite.

        El free tier permite 3 peticiones por minuto. Primero se pide turno —que
        es no llegar al limite— y recien despues se reintenta, que es reaccionar
        cuando ya se llego. El reintento sigue estando porque el turno se reparte
        por proceso y el limite lo cuenta el proveedor: dos instancias del mismo
        deploy no se ven entre si.
        """
        client = self._ensure_loaded()
        self._limitador.esperar_turno(PROVEEDOR_EMBEDDINGS, EMBEDDING_RPM)
        for intento in range(EMBEDDING_RATE_LIMIT_RETRIES + 1):
            try:
                return client.embed(textos, model=self._model_name).embeddings
            except Exception as e:
                if type(e).__name__ != "RateLimitError":
                    logger.error("Voyage AI embed() failed for %d texts: %s", len(textos), e)
                    raise
                if intento == EMBEDDING_RATE_LIMIT_RETRIES:
                    logger.warning(
                        "Voyage AI: limite de rate tras %d intentos, %d textos sin vector",
                        intento + 1, len(textos),
                    )
                    raise EmbeddingRateLimit(str(e)) from e
                logger.info(
                    "Voyage AI: limite de rate, reintento %d en %ds",
                    intento + 1, EMBEDDING_RATE_LIMIT_WAIT_S,
                )
                time.sleep(EMBEDDING_RATE_LIMIT_WAIT_S)
        raise AssertionError("inalcanzable")  # pragma: no cover

    def _cachear(self, vectores: list[list[float]], textos: list[str]) -> None:
        # Cache acotado: el corpus de consultas es chico y repetitivo, pero un
        # proceso largo no deberia crecer sin limite.
        if len(self._cache) + len(textos) > EMBEDDING_CACHE_MAX:
            self._cache.clear()
        for texto, vector in zip(textos, vectores, strict=True):
            self._cache[texto] = np.array(vector, dtype=np.float32)
