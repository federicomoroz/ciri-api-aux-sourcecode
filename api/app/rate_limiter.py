"""Un limitador de tasa del lado del cliente: no más de N llamadas por ventana.

**Preventivo, no reactivo.** Reintentar un 429 es reaccionar tarde: la llamada ya
se gastó y el proveedor ya la rechazó. Si el límite se conoce de antemano —y en
los free tier se conoce— conviene no llegar.

Ventana deslizante y no un intervalo fijo: mientras se esté por debajo del techo
no se espera nada, y recién cuando la ventana está llena se duerme hasta que la
llamada más vieja cumpla el minuto. Un intervalo fijo le agregaría latencia a la
primera investigación, que es justo la que alguien va a estar mirando.

**Por qué es un componente y no un método privado.** Esto vivía dentro de
`LLMManager`, así que solo lo tenía el camino del modelo. El de embeddings no, y
Voyage tiene un free tier de 3 peticiones por minuto — el más ajustado de todo el
sistema. El resultado se midió en producción: una tanda de análisis seguidos comió
un 429 de Voyage que este componente habría evitado. Una capacidad que existe una
vez y hace falta dos es un componente.

Lo que **no** es: un planificador. No ejecuta acciones diferidas ni agenda nada.
De orquestar se encarga n8n, y de esperar a una persona su nodo `Wait`. Meter
ejecución diferida acá duplicaría el trabajo que esta arquitectura deja afuera a
propósito.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

SIN_LIMITE = 0


class RateLimiter:
    """Reparte turnos para que ninguna clave se pase de su tasa.

    Cada clave —un proveedor, un servicio— lleva su propia ventana: que Gemini
    esté en su techo no tiene por qué frenar una llamada a Groq.

    Es seguro entre hilos: las rutas sincrónicas de FastAPI corren en un pool, así
    que dos investigaciones simultáneas comparten esta instancia.
    """

    def __init__(
        self,
        ventana_s: float = 60.0,
        pausa_minima_s: float = 0.05,
        *,
        reloj: Callable[[], float] = time.monotonic,
        dormir: Callable[[float], None] = time.sleep,
    ):
        # `reloj` y `dormir` se inyectan para que los tests puedan comprobar el
        # reparto de turnos sin dormir de verdad: un test que tarda un minuto en
        # verificar un limite de un minuto no se corre nunca.
        self._ventana_s = ventana_s
        self._pausa_minima_s = pausa_minima_s
        self._reloj = reloj
        self._dormir = dormir
        self._llamadas: dict[str, deque[float]] = {}
        self._candado = threading.Lock()

    def esperar_turno(self, clave: str, limite: int) -> float:
        """Bloquea hasta que esa clave pueda llamar. Devuelve cuánto se esperó.

        Con `limite <= 0` no hay techo conocido y se pasa de largo: es el caso de
        los proveedores de pago, donde la cuota es de la cuenta y no del modelo.
        """
        if limite <= SIN_LIMITE:
            return 0.0

        esperado = 0.0
        while True:
            with self._candado:
                ahora = self._reloj()
                ventana = self._llamadas.setdefault(clave, deque())
                while ventana and ahora - ventana[0] >= self._ventana_s:
                    ventana.popleft()
                if len(ventana) < limite:
                    ventana.append(ahora)
                    return esperado
                falta = self._ventana_s - (ahora - ventana[0])

            logger.info(
                "%s esta en su limite de %d por %.0fs: se espera %.1fs antes de llamar",
                clave, limite, self._ventana_s, falta,
            )
            pausa = max(falta, self._pausa_minima_s)
            self._dormir(pausa)
            esperado += pausa

    def turnos_usados(self, clave: str) -> int:
        """Cuántas llamadas hay dentro de la ventana. Para tests y diagnóstico."""
        with self._candado:
            ahora = self._reloj()
            ventana = self._llamadas.get(clave, deque())
            while ventana and ahora - ventana[0] >= self._ventana_s:
                ventana.popleft()
            return len(ventana)

    def olvidar(self, clave: str | None = None) -> None:
        """Descarta lo registrado. Sin clave, todo."""
        with self._candado:
            if clave is None:
                self._llamadas.clear()
            else:
                self._llamadas.pop(clave, None)
