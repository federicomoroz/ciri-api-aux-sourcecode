"""Registra que una orquestacion de n8n llego hasta esta API.

Para que sirve: quien importa el workflow en su n8n y lo dispara no tiene forma
de saber, desde su lado, si la llamada llego. Con esto el panel puede decirle
"una automatizacion de n8n toco esta API hace 40 segundos", que es la
confirmacion de que el cableado quedo bien.

Lo que deliberadamente NO guarda: de donde vino. Esta API es publica y
compartida; anotar la URL del n8n de quien la prueba y despues mostrarsela a
otro seria filtrar su instancia. Solo interesa que ocurrio y cuando.

Tampoco persiste: vive en memoria y se pierde al reiniciar. Es una senal de
"recien paso esto", no un registro historico — para eso estan las alertas.
"""

import threading
import time


class ContactoN8n:
    """Cuando fue la ultima vez que una orquestacion de n8n llamo a la API."""

    def __init__(self, reloj=time.monotonic) -> None:
        self._reloj = reloj
        self._ultimo: float | None = None
        self._total = 0
        self._candado = threading.Lock()

    def registrar(self) -> None:
        with self._candado:
            self._ultimo = self._reloj()
            self._total += 1

    def hace_cuanto(self) -> float | None:
        """Segundos desde el ultimo contacto, o None si todavia no hubo ninguno."""
        with self._candado:
            if self._ultimo is None:
                return None
            return max(0.0, self._reloj() - self._ultimo)

    @property
    def total(self) -> int:
        with self._candado:
            return self._total
