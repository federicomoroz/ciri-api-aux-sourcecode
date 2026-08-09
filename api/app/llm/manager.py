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
from ..domain.constants import (
    LLM_DEFAULT_MAX_TOKENS,
    LLM_DEFAULT_TEMPERATURE,
    LLM_RPM_PAUSA_MINIMA_S,
    LLM_RPM_VENTANA_S,
)
from ..observability.tracer import Tracer
from ..rate_limiter import RateLimiter
from .client import Ajustes, AnthropicClient, Cuota, LLMClient, OpenAICompatibleClient
from .perfiles import perfil_de
from .proveedores import PROVEEDOR_ANTHROPIC, base_url_de, existe

logger = logging.getLogger(__name__)


class LLMManager:
    """Fabrica y ciclo de vida de los clientes de modelo."""

    def __init__(self, settings: Settings, tracer: Tracer, limitador: RateLimiter | None = None):
        self.settings = settings
        self.tracer = tracer
        self._cache: dict[tuple[str, str], LLMClient] = {}
        # El reparto de turnos vive en `rate_limiter.py`: lo necesita tambien el
        # camino de embeddings, y mientras fue un metodo privado de aca no lo
        # tenia. Se puede compartir una instancia entre ambos.
        self.limitador = limitador or RateLimiter(
            ventana_s=LLM_RPM_VENTANA_S, pausa_minima_s=LLM_RPM_PAUSA_MINIMA_S,
        )

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
        if proveedor == PROVEEDOR_ANTHROPIC:
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

    def _ajustes(self) -> Ajustes:
        """`CB_LLM_TEMPERATURE` y `CB_LLM_MAX_TOKENS`, con los que corre el cliente.

        `getattr` y no acceso directo, igual que `clave_de` y `rpm_de`: lo que
        llega como `settings` no siempre es la clase `Settings` —los tests y los
        scripts pasan objetos con la superficie que necesitan—.
        """
        return Ajustes(
            temperature=getattr(self.settings, "llm_temperature", LLM_DEFAULT_TEMPERATURE),
            max_tokens=getattr(self.settings, "llm_max_tokens", LLM_DEFAULT_MAX_TOKENS),
        )

    def _cuota(self, proveedor: str, modelo: str) -> Cuota:
        """El reparto de turnos de este proveedor, compartido entre todos sus clientes."""
        return Cuota(
            proveedor=proveedor or PROVEEDOR_ANTHROPIC,
            rpm=self.rpm_de(proveedor, modelo),
            limitador=self.limitador,
        )

    def _construir(self, proveedor: str, modelo: str, api_key: str) -> LLMClient:
        clave = api_key or self.clave_de(proveedor)
        base = base_url_de(
            proveedor,
            self.settings.llm_base_url if proveedor == PROVEEDOR_ANTHROPIC else "",
        )
        if proveedor != PROVEEDOR_ANTHROPIC and not base:
            base = base_url_de(proveedor) or self.settings.llm_base_url
        if not base:
            # Sin URL solo puede ser Anthropic, que tiene SDK propio. Cualquier
            # otro proveedor sin URL es uno que no esta en el registro, y caer
            # aca en silencio mandaba su modelo y su credencial a la API de
            # Anthropic: el error hablaba de autenticacion y no del proveedor
            # que faltaba. Se dice cual es.
            if proveedor and proveedor != PROVEEDOR_ANTHROPIC and not existe(proveedor):
                raise ValueError(
                    f"proveedor desconocido: {proveedor!r}. Los que el sistema "
                    f"sabe construir estan en `llm/proveedores.py`; para uno "
                    f"nuevo, agregarlo ahi o pasar CB_LLM_BASE_URL."
                )
            return AnthropicClient(
                api_key=clave, model=modelo,
                tracer=self.tracer, max_retries=self.settings.llm_max_retries,
                cuota=self._cuota(proveedor, modelo), ajustes=self._ajustes(),
            )
        return OpenAICompatibleClient(
            api_key=clave, model=modelo, base_url=base,
            tracer=self.tracer, max_retries=self.settings.llm_max_retries,
            proveedor=proveedor,
            cuota=self._cuota(proveedor, modelo), ajustes=self._ajustes(),
        )

    def completar(
        self, proveedor: str, modelo: str, system: str, user: str,
        *, api_key: str = "", trace_id: str | None = None, **extra,
    ):
        """Le pide una respuesta al modelo que corresponda. Es LA puerta.

        Nadie fuera de esta clase toca un cliente. Los llamadores dicen que
        proveedor y que modelo —o mejor, dicen que PASO y otro lo traduce— y
        reciben un `LLMResult`. La diferencia no es estetica: mientras el que
        llama pueda elegir el cliente, puede elegir el equivocado, y en un
        pipeline de tres pasos eso significa media investigacion corriendo en un
        proveedor y media en otro.

        Las diferencias entre modelos —cuanto presupuesto de tokens necesita uno
        que razona, que errores vale la pena reintentar— se resuelven adentro,
        que es donde se sabe con quien se esta hablando.

        **Este metodo ya no es el que aplica la cuota ni la configuracion.** Las
        aplica el cliente, porque no es la unica puerta: el pipeline efimero del
        panel —modo demo y BYOK— recibe clientes ya armados de `clientes_para()`
        / `clientes_demo()` y los invoca directo. Mientras el turno se pedia aca,
        ese camino no lo pedia, y es justamente el que corre sobre free tier:
        Gemini son 5 pedidos por minuto y una investigacion hace 3 llamadas, o
        sea un analisis por minuto. El segundo seguido rebotaba, y un 429 gasta
        cuota del tope diario igual que una llamada que sirve.
        """
        return self.cliente(proveedor, modelo, api_key).complete(
            system, user, trace_id=trace_id, **extra,
        )

    def rpm_de(self, proveedor: str, modelo: str) -> int:
        """Pedidos por minuto que se le permiten a esa combinacion.

        El perfil pone el piso conocido de la familia; `CB_LLM_RPM` lo pisa,
        porque la cuota es de la cuenta y no del modelo: quien pague un plan no
        tiene por que arrastrar el techo del free tier.
        """
        override = (getattr(self.settings, "llm_rpm", None) or {}).get(proveedor)
        if override is not None:
            return int(override)
        return perfil_de(proveedor, modelo).pedidos_por_minuto


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
