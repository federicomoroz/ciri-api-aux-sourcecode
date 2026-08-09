"""Un tracer que anota en SQLite, para cuando Langfuse no esta configurado.

Langfuse es opcional: pide dos claves y una cuenta, y el que evalue el sistema
no tiene por que crearse una. Hasta ahora, sin esas claves el panel escondia la
seccion de observabilidad entera y no se medra nada — ni tokens, ni latencia, ni
la nota del juez.

Eso confunde dos cosas distintas. **Que no haya Langfuse no significa que no
haya nada que medir**: la latencia de cada llamada existe igual, los tokens los
informa el proveedor, y el costo de una corrida gratuita es un dato —cero— y no
una ausencia. Langfuse aporta la traza distribuida, el historico largo y la UI;
no aporta los numeros basicos, que ya pasan por aca.

Implementa el mismo `Tracer` Protocol que `LangfuseTracer`, asi que nadie
aguas arriba sabe cual de los dos esta corriendo. La diferencia se declara en el
panel, que dice de donde salen los numeros: no es lo mismo «medido en este
proceso» que «lo que registro Langfuse».

Lo que NO hace: retencion, agregacion por periodo, ni sobrevivir a un reinicio
en Render, donde la SQLite es efimera. Para eso esta Langfuse.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from ..domain.constants import SQLITE_TIMEOUT_S
from .resumen import resumir_trazas

logger = logging.getLogger(__name__)

# Las tablas se declaran en `data/esquema.py` junto con el resto de la base. Este
# modulo es el unico que las escribe, pero son parte de la forma del mismo
# archivo SQLite: tenerlas aparte hacia que «que tablas hay» no se pudiera
# contestar leyendo un solo lugar.


class TrazadorLocal:
    """Anota trazas, generaciones y puntajes en la misma SQLite del proyecto."""

    def __init__(self, sqlite_path: str):
        self.sqlite_path = sqlite_path
        # Cada llamada abre su conexion: el pipeline corre pasos en hilos y una
        # conexion de sqlite3 no se comparte entre ellos.
        self._candado = threading.Lock()
        self._preparar()

    @property
    def enabled(self) -> bool:
        """Siempre. Que no haya Langfuse no lo apaga: es justamente su caso."""
        return True

    def _preparar(self) -> None:
        # Import local, no perezoso por necesidad: no hay ciclo —`data.esquema`
        # solo importa de `domain`—. Esta aca porque este modulo se construye en
        # el arranque, antes de que la base exista, y mantenerlo local deja la
        # dependencia donde se usa: lo unico que necesita del esquema son sus
        # tres tablas.
        from ..data.esquema import OBSERVABILIDAD

        try:
            with self._conexion() as c:
                for ddl in OBSERVABILIDAD:
                    c.execute(ddl)
        except Exception:
            logger.warning("No se pudo preparar el registro local de trazas", exc_info=True)

    @contextmanager
    def _conexion(self) -> Iterator[sqlite3.Connection]:
        """Una conexion que se cierra al salir.

        `with sqlite3.connect(...)` cierra la TRANSACCION, no la conexion: el
        tracer abria una por cada anotacion y no cerraba ninguna. Observar el
        sistema no puede ser una forma nueva de romperlo, y quedarse con los
        descriptores abiertos lo es.
        """
        conexion = sqlite3.connect(self.sqlite_path, timeout=SQLITE_TIMEOUT_S)
        try:
            with conexion:
                yield conexion
        finally:
            conexion.close()

    def _escribir(self, sql: str, params: tuple) -> None:
        """Anotar nunca puede tumbar un analisis: se registra el fallo y sigue.

        Observar el sistema no puede ser una forma nueva de romperlo.
        """
        try:
            with self._candado, self._conexion() as c:
                c.execute(sql, params)
        except Exception:
            logger.debug("No se pudo anotar en el registro local", exc_info=True)

    # ── Protocolo Tracer ────────────────────────────────────────────────

    def trace(self, name: str, input: dict, output: dict, metadata: dict | None = None) -> str:
        trace_id = uuid.uuid4().hex
        self._escribir(
            "INSERT INTO trazas (trace_id, nombre, metadata) VALUES (?, ?, ?)",
            (trace_id, name, json.dumps(metadata or {}, ensure_ascii=False, default=str)),
        )
        return trace_id

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
        # El texto del prompt y de la respuesta no se guardan: no hacen falta
        # para ningun numero del panel y son datos del caso.
        self._escribir(
            "INSERT INTO generaciones (trace_id, nombre, modelo, tokens_in, tokens_out, "
            "latency_ms) VALUES (?, ?, ?, ?, ?, ?)",
            (trace_id or "", name, model, int(tokens_in), int(tokens_out), float(latency_ms)),
        )

    def score(self, trace_id: str, name: str, value: float) -> None:
        if not trace_id:
            return
        self._escribir(
            "INSERT OR REPLACE INTO puntajes (trace_id, nombre, valor) VALUES (?, ?, ?)",
            (trace_id, name, float(value)),
        )

    # ── Lectura, para el panel ──────────────────────────────────────────

    def resumen(self, limite: int) -> tuple[dict, list[dict]]:
        """Los mismos numeros que el panel le pide a Langfuse.

        Se agrupa por traza y no por llamada: una traza es un analisis, que son
        tres llamadas al modelo. La latencia que importa es la del analisis
        entero, que es lo que espera quien lo pidio.
        """
        try:
            with self._conexion() as c:
                c.row_factory = sqlite3.Row
                filas = c.execute(
                    "SELECT t.trace_id, t.nombre, t.created_at, "
                    "  COALESCE(SUM(g.tokens_in), 0)  AS tokens_in, "
                    "  COALESCE(SUM(g.tokens_out), 0) AS tokens_out, "
                    "  COALESCE(SUM(g.latency_ms), 0) AS latency_ms, "
                    "  MAX(g.modelo)                  AS modelo, "
                    "  (SELECT valor FROM puntajes p WHERE p.trace_id = t.trace_id "
                    "   ORDER BY p.created_at DESC LIMIT 1) AS score "
                    "FROM trazas t LEFT JOIN generaciones g ON g.trace_id = t.trace_id "
                    "GROUP BY t.trace_id ORDER BY t.created_at DESC LIMIT ?",
                    (limite,),
                ).fetchall()
        except Exception:
            logger.debug("No se pudo leer el registro local", exc_info=True)
            return {}, []

        if not filas:
            return {}, []

        from ..llm.pricing import estimar_costo_usd

        recientes = [
            {
                "trace_id": f["trace_id"],
                "name": f["nombre"],
                "tokens": f["tokens_in"] + f["tokens_out"],
                "latency_s": round(f["latency_ms"] / 1000, 2),
                "score": f["score"],
                "model": f["modelo"] or "",
            }
            for f in filas
        ]
        # La forma que espera `resumir_trazas`. El costo no se guarda: se
        # estima al leer, con la tarifa vigente del modelo que corrio.
        normalizadas = [
            {
                "tokens_in": f["tokens_in"],
                "tokens_out": f["tokens_out"],
                "cost_usd": estimar_costo_usd(f["modelo"] or "", f["tokens_in"], f["tokens_out"]),
                "latency_s": f["latency_ms"] / 1000,
                "score": f["score"],
            }
            for f in filas
        ]
        return resumir_trazas(normalizadas), recientes
