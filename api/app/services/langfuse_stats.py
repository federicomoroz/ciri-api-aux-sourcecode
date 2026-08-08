"""
Langfuse stats query service.

Reads trace/generation/score data from Langfuse for display in the test panel.
Uses a TTL cache to avoid hammering the Langfuse API on every panel refresh.

IMPORTANT: Langfuse free tier has a 15 req/min rate limit per endpoint.
This service uses at most 3 API calls (traces + observations + scores)
instead of N+1 per-trace calls.
"""

import logging
import time
from collections import defaultdict

from ..domain.constants import (
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_STATS_CACHE_TTL_S,
    LANGFUSE_STATS_DISPLAY_LIMIT,
    LANGFUSE_STATS_FETCH_LIMIT,
    LANGFUSE_STATS_TRACE_LIMIT,
)
from ..llm.pricing import estimar_costo_usd
from ..observability.resumen import resumir_trazas
from ..observability.tracer import FuenteDeMetricas, Tracer

logger = logging.getLogger(__name__)


def _campo(obj, nombre: str, defecto=None):
    """Lee un campo de la respuesta del SDK, venga como objeto o como dict.

    Langfuse devuelve una cosa u otra segun la version y el endpoint. Sin esto,
    cada acceso repetia el mismo `hasattr(...) else .get(...)`.
    """
    if hasattr(obj, nombre):
        valor = getattr(obj, nombre)
        return defecto if valor is None else valor
    if isinstance(obj, dict):
        valor = obj.get(nombre, defecto)
        return defecto if valor is None else valor
    return defecto


class LangfuseStatsService:
    """Query Langfuse for observability stats shown in the test panel."""

    def __init__(self, tracer: Tracer, model_name: str):
        self._tracer = tracer
        self._model_name = model_name
        self._cache: dict | None = None
        self._cache_time: float = 0

    @property
    def enabled(self) -> bool:
        """Si hay un Langfuse vivo al que preguntarle.

        `enabled` del tracer significa «observa de verdad», no «se configuro»:
        `LangfuseTracer` se apaga solo en cuanto una llamada falla. Sin eso, esta
        propiedad seguia dando True con Langfuse caido y el panel mostraba la
        fuente remota vacia en vez del registro local, que si tenia datos.

        El `hasattr(tracer, "langfuse")` sigue siendo la unica forma honesta de
        preguntar «¿este tracer habla con el servicio remoto?»: la capacidad no
        es parte de `Tracer` —escribir— ni de `FuenteDeMetricas` —leer el
        historico propio—, porque el historico de Langfuse se consulta con la API
        de Langfuse y de eso se encarga esta clase.
        """
        return bool(self._tracer.enabled) and hasattr(self._tracer, "langfuse")

    def get_stats(self) -> dict:
        if not self.enabled:
            return self._stats_locales()

        now = time.time()
        if self._cache and (now - self._cache_time) < LANGFUSE_STATS_CACHE_TTL_S:
            return self._cache

        try:
            result = self._fetch_stats()
            self._cache = result
            self._cache_time = now
            return result
        except Exception as e:
            logger.warning("Langfuse stats query failed: %s", e)
            return {"enabled": True, "summary": None, "recent_traces": []}

    # ── Lectura: tres llamadas a la API, ninguna por traza ──────────────

    def _traer_trazas(self) -> list:
        resp = self._tracer.langfuse.fetch_traces(limit=LANGFUSE_STATS_TRACE_LIMIT)
        return _campo(resp, "data", [])

    def _observaciones_por_traza(self) -> dict[str, list]:
        """Todas las generaciones recientes de una vez, agrupadas por traza."""
        por_traza: dict[str, list] = defaultdict(list)
        try:
            resp = self._tracer.langfuse.fetch_observations(
                type=LANGFUSE_OBSERVATION_TYPE, limit=LANGFUSE_STATS_FETCH_LIMIT,
            )
            for obs in _campo(resp, "data", []):
                tid = _campo(obs, "trace_id", "")
                if tid:
                    por_traza[tid].append(obs)
        except Exception as e:
            logger.debug("Failed to fetch observations: %s", e)
        return por_traza

    def _puntajes_por_traza(self) -> dict[str, float]:
        puntajes: dict[str, float] = {}
        try:
            resp = self._tracer.langfuse.client.score.get(limit=LANGFUSE_STATS_FETCH_LIMIT)
            for s in _campo(resp, "data", []):
                tid, valor = _campo(s, "trace_id", ""), _campo(s, "value")
                if tid and valor is not None:
                    puntajes[tid] = float(valor)
        except Exception as e:
            logger.warning("Failed to fetch scores: %s", e)
        return puntajes

    # ── Agregacion ──────────────────────────────────────────────────────

    def _resumir_traza(self, traza, observaciones: list, puntaje: float | None) -> dict:
        """Tokens, latencia, costo y puntaje de una traza.

        El costo se acumula por observacion y con el modelo de cada una. Sumar
        todos los tokens y aplicarles un unico precio cotizaba a tarifa de Haiku
        las llamadas que corren en Sonnet: en la configuracion de produccion son
        dos de las tres, y son las caras.
        """
        entrada = salida = 0
        latencia = costo = 0.0
        for obs in observaciones:
            uso = _campo(obs, "usage", {})
            obs_in = _campo(uso, "input", 0)
            obs_out = _campo(uso, "output", 0)
            entrada += obs_in
            salida += obs_out
            latencia += _campo(obs, "latency", 0)
            modelo = _campo(obs, "model", "")
            costo += estimar_costo_usd(
                (modelo if isinstance(modelo, str) else "") or self._model_name,
                obs_in, obs_out,
            )
        return {
            "trace_id": str(_campo(traza, "id", "")),
            "name": str(_campo(traza, "name", "")),
            "timestamp": str(_campo(traza, "timestamp", "")),
            "tokens_in": entrada,
            "tokens_out": salida,
            "tokens": entrada + salida,
            "cost_usd": round(costo, 6),
            "latency_s": round(latencia, 2),
            "score": puntaje,
        }

    def _resumen(self, filas: list[dict]) -> dict:
        return resumir_trazas(filas)

    def _stats_locales(self) -> dict:
        """Lo que se pudo medir sin Langfuse. No es lo mismo, y se declara.

        Sin Langfuse el panel escondia la seccion entera, como si no hubiera
        nada que mostrar. Pero la latencia de cada llamada existe igual, los
        tokens los informa el proveedor, y el costo de una corrida gratuita es
        un dato —cero— y no una ausencia. Lo que falta es la traza distribuida y
        el historico, no los numeros.

        `fuente` va en la respuesta para que el panel diga de donde salen: no es
        lo mismo «medido en este proceso» que «lo que registro Langfuse».
        """
        if not isinstance(self._tracer, FuenteDeMetricas):
            # No todo el que escribe puede leer: `NoOpTracer` no guarda nada.
            return {"enabled": False, "fuente": "local", "summary": None, "recent_traces": []}
        resumen, recientes = self._tracer.resumen(LANGFUSE_STATS_FETCH_LIMIT)
        if not resumen:
            return {"enabled": False, "fuente": "local", "summary": None, "recent_traces": []}
        return {
            "enabled": True,
            "fuente": "local",
            "summary": resumen,
            "recent_traces": recientes[:LANGFUSE_STATS_DISPLAY_LIMIT],
        }

    def _fetch_stats(self) -> dict:
        trazas = self._traer_trazas()
        if not trazas:
            return {"enabled": True, "summary": self._resumen([]), "recent_traces": []}

        observaciones = self._observaciones_por_traza()
        puntajes = self._puntajes_por_traza()

        filas = [
            self._resumir_traza(
                t, observaciones.get(str(_campo(t, "id", "")), []),
                puntajes.get(str(_campo(t, "id", ""))),
            )
            for t in trazas
        ]
        recent = [{k: v for k, v in f.items() if k not in ("tokens_in", "tokens_out")} for f in filas]
        summary = self._resumen(filas)

        return {
            "enabled": True,
            "summary": summary,
            "recent_traces": recent[:LANGFUSE_STATS_DISPLAY_LIMIT],
        }

