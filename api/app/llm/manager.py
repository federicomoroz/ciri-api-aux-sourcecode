"""Quien habla con los proveedores de modelos.

Separacion de responsabilidades, en dos frases:

- **`ModelosService`** (en `services/modelos.py`) sabe *que se eligio*: lee y
  escribe la configuracion por paso en SQLite, conoce los defaults y arma el
  catalogo que muestra el panel. No sabe que existe HTTP.
- **`LLMManager`** (aca) sabe *como se le habla a eso*: resuelve credenciales,
  construye el cliente que corresponda al proveedor, lo cachea, lo cierra, y
  entrega servicios listos para usar. No sabe que existe SQLite ni que el
  pipeline tiene tres pasos.

La razon de que esto sea un componente con nombre y no funciones sueltas: hubo
un momento en que tres lugares distintos —una ruta, el panel y un script—
ensamblaban clientes por su cuenta, y alcanzo con que uno quedara desfasado para
que el panel dijera una cosa y n8n hiciera otra. Con un solo lugar que los
fabrica, esa clase de divergencia no tiene donde aparecer.

**Las claves nunca se persisten.** Salen del entorno o viajan por peticion.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from ..config import Settings
from ..domain.constants import (
    LLM_RPM_PAUSA_MINIMA_S,
    LLM_RPM_SIN_LIMITE,
    LLM_RPM_VENTANA_S,
)
from ..observability.tracer import Tracer
from .adaptadores import adaptador_de
from .client import AnthropicClient, LLMClient, OpenAICompatibleClient, base_url_de

logger = logging.getLogger(__name__)


class LLMManager:
    """Fabrica y ciclo de vida de los clientes de modelo."""

    def __init__(self, settings: Settings, tracer: Tracer):
        self.settings = settings
        self.tracer = tracer
        self._cache: dict[tuple[str, str], LLMClient] = {}
        # Cuando se llamo a cada proveedor, para no pasarse de su frecuencia.
        # Las rutas sincronicas de FastAPI corren en un pool de hilos, asi que
        # dos investigaciones simultaneas comparten esto y hay que sincronizar.
        self._llamadas: dict[str, deque[float]] = {}
        self._candado = threading.Lock()

    # ── Credenciales ────────────────────────────────────────────────────

    def clave_de(self, proveedor: str) -> str:
        """La credencial del servidor para ese proveedor, si la hay.

        De lo especifico a lo general: la del proveedor, la generica, y para
        Anthropic su variable de siempre. Asi el dueno del deploy puede cargar
        las de free tier —que no arriesgan nada— y dejar las de pago afuera,
        para que cada visitante traiga la suya.
        """
        por_proveedor = (getattr(self.settings, "llm_api_keys", None) or {}).get(proveedor, "")
        if por_proveedor:
            return por_proveedor
        if proveedor == "anthropic":
            return self.settings.anthropic_api_key
        return self.settings.llm_api_key

    def hay_clave(self, proveedor: str) -> bool:
        return bool(self.clave_de(proveedor))

    # ── Clientes ────────────────────────────────────────────────────────

    def cliente(self, proveedor: str, modelo: str, api_key: str = "") -> LLMClient:
        """El cliente de ese proveedor y modelo.

        Con `api_key` se construye uno efimero y **no se cachea**: la credencial
        es de quien hizo la peticion, no del proceso.
        """
        if api_key:
            return self._construir(proveedor, modelo, api_key)
        llave = (proveedor, modelo)
        if llave not in self._cache:
            self._cache[llave] = self._construir(proveedor, modelo, "")
        return self._cache[llave]

    def _construir(self, proveedor: str, modelo: str, api_key: str) -> LLMClient:
        clave = api_key or self.clave_de(proveedor)
        base = base_url_de(proveedor, self.settings.llm_base_url if proveedor == "anthropic" else "")
        if proveedor != "anthropic" and not base:
            base = base_url_de(proveedor) or self.settings.llm_base_url
        if not base:
            return AnthropicClient(
                api_key=clave, model=modelo,
                tracer=self.tracer, max_retries=self.settings.llm_max_retries,
            )
        return OpenAICompatibleClient(
            api_key=clave, model=modelo, base_url=base,
            tracer=self.tracer, max_retries=self.settings.llm_max_retries,
            proveedor=proveedor,
        )

    def completar(
        self, proveedor: str, modelo: str, system: str, user: str,
        *, api_key: str = "", trace_id: str | None = None, **extra,
    ):
        """Le pide una respuesta al modelo que corresponda. Es LA puerta.

        Nadie fuera de esta clase toca un cliente. Los llamadores dicen que
        proveedor y que modelo —o mejor, dicen que PASO y otro lo traduce— y
        reciben un `LLMResult`. La diferencia no es estetica: mientras el que
        llama pueda elegir el cliente, puede elegir el equivocado, y eso ya paso.
        El juez resolvia el servicio del modo demo y despues llamaba al de
        produccion, asi que la mitad del pipeline se iba por Anthropic sin
        credito mientras la otra mitad corria en Gemini.

        Las diferencias entre modelos —cuanto presupuesto de tokens necesita uno
        que razona, que errores vale la pena reintentar— se resuelven adentro,
        que es donde se sabe con quien se esta hablando.
        """
        self._espaciar(proveedor, modelo)
        return self.cliente(proveedor, modelo, api_key).complete(
            system, user, trace_id=trace_id, **extra,
        )

    def rpm_de(self, proveedor: str, modelo: str) -> int:
        """Pedidos por minuto que se le permiten a esa combinacion.

        El adaptador pone el piso conocido de la familia; `CB_LLM_RPM` lo pisa,
        porque la cuota es de la cuenta y no del modelo: quien pague un plan no
        tiene por que arrastrar el techo del free tier.
        """
        override = (getattr(self.settings, "llm_rpm", None) or {}).get(proveedor)
        if override is not None:
            return int(override)
        return adaptador_de(proveedor, modelo).pedidos_por_minuto

    def _espaciar(self, proveedor: str, modelo: str) -> None:
        """Espera lo justo para no pasarse de los pedidos por minuto.

        Reintentar un 429 es reaccionar tarde: la llamada ya se gasto y el
        proveedor ya la rechazo. Si el limite se conoce, conviene no llegar.

        Ventana deslizante y no un intervalo fijo: mientras se este por debajo
        del techo no se espera nada, y recien cuando la ventana esta llena se
        duerme hasta que la llamada mas vieja cumpla el minuto. Un intervalo
        fijo le agregaria latencia a la primera investigacion, que es justo la
        que alguien va a estar mirando.
        """
        limite = self.rpm_de(proveedor, modelo)
        if limite <= LLM_RPM_SIN_LIMITE:
            return
        while True:
            with self._candado:
                ahora = time.monotonic()
                ventana = self._llamadas.setdefault(proveedor, deque())
                while ventana and ahora - ventana[0] >= LLM_RPM_VENTANA_S:
                    ventana.popleft()
                if len(ventana) < limite:
                    ventana.append(ahora)
                    return
                espera = LLM_RPM_VENTANA_S - (ahora - ventana[0])
            logger.info(
                "%s esta en su limite de %d/min: se espera %.1fs antes de llamar",
                proveedor, limite, espera,
            )
            time.sleep(max(espera, LLM_RPM_PAUSA_MINIMA_S))

    def invalidar(self) -> None:
        """Suelta los clientes cacheados para que el proximo se arme de nuevo."""
        for cliente in self._cache.values():
            self._cerrar(cliente)
        self._cache.clear()

    @staticmethod
    def _cerrar(cliente: LLMClient | None) -> None:
        cerrar = getattr(cliente, "close", None)
        if cerrar:
            try:
                cerrar()
            except Exception:
                logger.debug("No se pudo cerrar un cliente", exc_info=True)

    def es_compartido(self, cliente: LLMClient) -> bool:
        """Si este cliente esta en el cache y lo van a reusar otras peticiones."""
        return any(c is cliente for c in self._cache.values())

    def cerrar_todos(self, clientes) -> None:
        """Cierra los clientes de una peticion, **salteando los compartidos**.

        Un cliente construido sin clave propia sale del cache y lo reusa la
        proxima peticion. Cerrarlo al terminar dejaba su pool de conexiones
        inservible: la primera peticion andaba y la segunda moria con
        «Cannot send a request, as the client has been closed». El panel no lo
        veia porque cada visitante traia su clave —esos si son efimeros— y
        aparecio recien cuando el modo demo empezo a correr con la clave del
        servidor.

        El llamador no tiene por que saber cual es cual: pide cerrar y aca se
        decide.
        """
        vistos = set()
        for cliente in (clientes or {}).values():
            if id(cliente) in vistos or self.es_compartido(cliente):
                continue
            vistos.add(id(cliente))
            self._cerrar(cliente)
