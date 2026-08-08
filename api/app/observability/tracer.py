"""
Observability tracer abstraction.

Axis 7 (Observabilidad): every LLM call, API request, judge score,
and cache hit is traced for cost/quality/latency monitoring.

LangfuseTracer: sends data to Langfuse cloud dashboard.
NoOpTracer: used in tests and when langfuse_enabled=False.
"""

import datetime
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class Tracer(Protocol):
    """Escribir observaciones. Lo que usa el pipeline mientras corre.

    Es la mitad del contrato. La otra —leer lo observado para el panel— esta en
    `FuenteDeMetricas`, y estan separadas porque no todo el que escribe puede
    leer: `NoOpTracer` no guarda nada y `LangfuseTracer` guarda en un servicio
    remoto que se consulta distinto que la SQLite local.

    Mientras fue un solo Protocol, `LangfuseStatsService` tenia que sortearlo con
    `hasattr(tracer, "langfuse")` y `getattr(tracer, "resumen", ...)` para
    averiguar con cual estaba hablando. Un Protocol que el consumidor tiene que
    esquivar dejo de ser el contrato.
    """

    @property
    def enabled(self) -> bool:
        """Si observa de verdad.

        **No es «se configuro», es «funciona».** De esto depende
        `LangfuseStatsService` para decidir si consulta el servicio remoto o cae
        al registro local: un tracer que dice `True` mientras sus llamadas
        fallan deja al panel mostrando la fuente vacia en vez de la que tiene
        datos. Una implementacion que empieza a fallar tiene que pasar a `False`.
        """
        ...

    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        """Create a trace. Returns trace_id."""
        ...

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Log an LLM generation with token counts and latency."""
        ...

    def score(self, trace_id: str, name: str, value: float) -> None:
        """Attach a score to a trace (e.g., judge_score)."""
        ...


@runtime_checkable
class FuenteDeMetricas(Protocol):
    """Leer lo observado, para mostrarlo en el panel.

    La declara quien puede responder por su propio historico. `TrazadorLocal` la
    implementa porque guarda en SQLite y la puede consultar; `NoOpTracer` no,
    porque no guarda nada; `LangfuseTracer` tampoco, porque su historico vive en
    un servicio remoto con su propia API — de eso se encarga
    `LangfuseStatsService`.
    """

    def resumen(self, limite: int) -> tuple[dict, list[dict]]:
        """(resumen agregado, ultimas trazas). Vacio si no hay nada anotado."""
        ...


class LangfuseTracer:
    """Real Langfuse integration for production observability."""

    def __init__(self, public_key: str, secret_key: str, host: str):
        try:
            from langfuse import Langfuse
            self.langfuse = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
            self._enabled = True
        except ImportError:
            logger.warning("langfuse package not installed; observability disabled")
            self._enabled = False
        except Exception as e:
            logger.warning("Langfuse init failed: %s; observability disabled", e)
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _rendirse(self, operacion: str, e: Exception) -> None:
        """Deja de decir que observa.

        Un tracer que anota el fallo y deja `_enabled` en `True` queda afirmando
        que observa mientras no anota nada. Y como `LangfuseStatsService` pregunta
        justamente eso para decidir si cae al registro local, **encender Langfuse
        dejaria al panel con menos metricas que apagarlo**.

        Se apaga a la primera y no se reintenta: los tres modos de falla reales
        —host equivocado, credenciales invalidas, una version del SDK que no tiene
        estos metodos— no se arreglan solos, y reintentar en cada llamada le suma
        latencia a cada paso del pipeline para volver a fallar.
        """
        if self._enabled:
            logger.warning(
                "Langfuse fallo en %s (%s): se desactiva la observabilidad remota y "
                "las metricas pasan a salir del registro local", operacion, e,
            )
        self._enabled = False

    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        if not self._enabled:
            return ""
        try:
            t = self.langfuse.trace(name=name, input=input, output=output, metadata=metadata or {})
            return t.id
        except Exception as e:
            self._rendirse("trace", e)
            return ""

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        try:
            end_time = datetime.datetime.now(datetime.UTC)
            start_time = end_time - datetime.timedelta(milliseconds=latency_ms)
            self.langfuse.generation(
                name=name,
                model=model,
                input=input,
                output=output,
                usage={"input": tokens_in, "output": tokens_out},
                start_time=start_time,
                end_time=end_time,
                trace_id=trace_id,
            )
        except Exception as e:
            self._rendirse("generation", e)

    def score(self, trace_id: str, name: str, value: float) -> None:
        if not self._enabled or not trace_id:
            return
        try:
            self.langfuse.score(trace_id=trace_id, name=name, value=value)
        except Exception as e:
            self._rendirse("score", e)


class NoOpTracer:
    """No-op tracer for tests and when observability is disabled."""

    @property
    def enabled(self) -> bool:
        return False

    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        return ""

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        pass

    def score(self, trace_id: str, name: str, value: float) -> None:
        pass
