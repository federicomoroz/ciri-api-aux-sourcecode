"""Lo que cada servicio necesita de sus dependencias, declarado.

Tres Protocols chicos que ya existian como contratos de hecho: se cumplian, pero
no estaban escritos, asi que se descubrian leyendo el codigo o —peor— con
`getattr(base.db, "save_alert", None)`.

**El criterio para escribir uno acá es que el consumidor use bastante menos de lo
que recibe.** `PipelineService` recibia `Database` con sus 29 metodos y usa 5;
`ResolutionService` recibia un `ModelosService` sin anotacion y usa un metodo.
Donde el consumidor usa casi todo —`FeedbackService`, `RAGUpdater`— sigue estando
la clase concreta, y eso es deliberado: hay un solo backend, no hay ORM, y cinco
Protocols con una implementacion cada uno es ceremonia y no inversion. Ver
`docs/decisions.md`.

Son `Protocol` y no clases base: nadie hereda de ellos. `Database` los cumple sin
saber que existen, que es justamente el punto — la dependencia apunta al
contrato, no al reves.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class FuenteDeCasos(Protocol):
    """Lo que el pipeline necesita leer y escribir para resolver un caso.

    Cinco operaciones de las veintinueve de `Database`. Declararlas dice cual es
    la superficie real: si mañana el pipeline necesita una sexta, agregarla acá
    es una decision visible en vez de una linea mas contra un objeto que ya lo
    permitia todo.
    """

    def get_transaction(self, txn_id: str) -> dict | None: ...

    def get_logs_for_transaction(self, tx_id: str) -> list[dict]: ...

    def get_case_for_transaction(self, txn_id: str) -> dict | None: ...

    def get_cached_report(self, key: str) -> str | None: ...

    def store_cached_report(self, key: str, html: str) -> None: ...


@runtime_checkable
class SumideroDeAlertas(Protocol):
    """Donde se anotan los eventos operativos que valen la pena mirar despues.

    `ResolutionService` lo recibe como callable y no como base, a proposito: ese
    servicio no tiene por que saber que existe SQLite. El panel lo sacaba de la
    base con `getattr(base.db, "save_alert", None)` — el contrato existia, solo
    que se descubria a mano.
    """

    def __call__(self, alerta: dict) -> int: ...


@runtime_checkable
class Completador(Protocol):
    """Quien traduce «el paso X» en una respuesta del modelo que le toca.

    Es la costura mas importante de `ResolutionService` y era el parametro
    `modelos=None`, sin anotacion. Lo que hay del otro lado es `ModelosService`,
    pero lo unico que este servicio necesita es esta llamada: no elige cliente ni
    proveedor, dice que paso es y recibe el resultado. Mientras el que llama
    pudiera elegir el cliente podia elegir el equivocado, y eso ya paso.
    """

    def completar(
        self, paso: str, system: str, user: str,
        *, demo: bool = False, api_key: str = "", trace_id: str | None = None, **extra,
    ): ...
