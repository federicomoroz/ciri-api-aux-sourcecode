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

from ..config import Settings
from ..observability.tracer import Tracer
from .client import AnthropicClient, LLMClient, OpenAICompatibleClient, base_url_de

logger = logging.getLogger(__name__)


class LLMManager:
    """Fabrica y ciclo de vida de los clientes de modelo."""

    def __init__(self, settings: Settings, tracer: Tracer):
        self.settings = settings
        self.tracer = tracer
        self._cache: dict[tuple[str, str], LLMClient] = {}

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
        )

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
