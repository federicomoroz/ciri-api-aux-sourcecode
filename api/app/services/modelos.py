"""Que modelo corre cada paso del pipeline.

El pipeline hace tres llamadas y cada una es una tarea distinta: comparar datos
contra reglas, redactar un analisis, y puntuar ese analisis con una rubrica. No
tienen por que correr en el mismo modelo — de hecho la configuracion documentada
ya usa dos— y elegirlo no deberia requerir un deploy.

Es la misma forma que las politicas: **el valor vigente vive en SQLite y el
default en `constants.py`**. El panel edita la tabla, este servicio traduce esa
eleccion en clientes y los cachea hasta que alguien la cambia.

Las claves NO se guardan. Viajan por peticion —el campo del panel— o salen del
entorno. Una instancia publica que persistiera claves ajenas seria un incidente
esperando: lo unico que se guarda es *que* modelo, nunca *con que credencial*.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..data.db import Database
from ..domain.constants import (
    PASO_JUEZ,
    PASO_POLITICAS,
    PASO_RESOLUCION,
    PASOS_DEL_PIPELINE,
    PASOS_DESCRIPCION,
    PROVEEDORES_SUGERIDOS,
)
from ..llm.client import LLMClient, base_url_de
from ..observability.tracer import Tracer

logger = logging.getLogger(__name__)


class ModelosService:
    """Resuelve, cachea y renueva el cliente de cada paso."""

    def __init__(self, db: Database, settings: Settings, tracer: Tracer, constructor):
        self.db = db
        self.settings = settings
        self.tracer = tracer
        # Se inyecta para no importar `dependencies` desde acá y cerrar un ciclo.
        self._constructor = constructor
        self._cache: dict[str, LLMClient] = {}

    # ── Configuracion ───────────────────────────────────────────────────

    def por_defecto(self) -> dict[str, dict[str, str]]:
        """Lo que dice `constants.py` y el `.env`, sin nada guardado encima."""
        proveedor = self.settings.llm_provider or "anthropic"
        sintesis = self.settings.llm_model_resolution or self.settings.llm_model
        return {
            PASO_POLITICAS: {"proveedor": proveedor, "modelo": self.settings.llm_model},
            PASO_RESOLUCION: {"proveedor": proveedor, "modelo": sintesis},
            PASO_JUEZ: {"proveedor": proveedor, "modelo": sintesis},
        }

    def vigente(self) -> dict[str, dict]:
        """La configuracion efectiva: lo guardado pisa al default, paso por paso."""
        defaults = self.por_defecto()
        try:
            guardado = self.db.get_modelos()
        except Exception:
            logger.warning("No se pudo leer la configuracion de modelos", exc_info=True)
            guardado = {}

        efectiva = {}
        for paso in PASOS_DEL_PIPELINE:
            elegido = guardado.get(paso)
            base = defaults[paso]
            efectiva[paso] = {
                **base,
                **({"proveedor": elegido["proveedor"], "modelo": elegido["modelo"]} if elegido else {}),
                "personalizado": elegido is not None,
                "actualizado": (elegido or {}).get("actualizado"),
                **PASOS_DESCRIPCION[paso],
            }
        return efectiva

    def guardar(self, paso: str, proveedor: str, modelo: str) -> None:
        if paso not in PASOS_DEL_PIPELINE:
            raise ValueError(f"paso desconocido: {paso}")
        if not modelo.strip():
            raise ValueError("el modelo no puede estar vacio")
        self.db.save_modelo(paso, proveedor.strip().lower(), modelo.strip())
        self.invalidar()
        logger.info("Modelo de %s -> %s / %s", paso, proveedor, modelo)

    def restablecer(self) -> None:
        self.db.reset_modelos()
        self.invalidar()

    def con_override(self, override: dict | None) -> dict[str, dict]:
        """La configuracion vigente con la eleccion de esta peticion encima.

        El panel manda su seleccion en cada analisis: vale para esa corrida y no
        cambia lo que ve nadie mas. En una instancia publica es la unica forma
        sensata — que un visitante cambie el modelo del proximo seria una
        sorpresa, y que no pueda probar otro seria inutil.
        """
        vigente = self.vigente()
        if not override:
            return vigente
        for paso, elegido in override.items():
            if paso not in vigente or not isinstance(elegido, dict):
                continue
            proveedor = str(elegido.get("proveedor") or vigente[paso]["proveedor"]).lower()
            modelo = str(elegido.get("modelo") or "").strip() or vigente[paso]["modelo"]
            vigente[paso] = {**vigente[paso], "proveedor": proveedor, "modelo": modelo,
                             "de_la_sesion": True}
        return vigente

    def clientes_para(self, override: dict | None, api_key: str = "") -> dict[str, LLMClient]:
        """Un cliente por paso para ESTA peticion. Nunca se cachean."""
        config = self.con_override(override)
        return {paso: self._construir(config[paso], api_key) for paso in PASOS_DEL_PIPELINE}

    # ── Clientes ────────────────────────────────────────────────────────

    def cliente(self, paso: str, api_key: str = "") -> LLMClient:
        """El cliente de ese paso.

        Con `api_key` se construye uno efimero —es el camino BYOK del panel— y
        no se cachea: la credencial es de quien hizo la peticion, no del proceso.
        """
        config = self.vigente()[paso]
        if api_key:
            return self._construir(config, api_key)
        if paso not in self._cache:
            self._cache[paso] = self._construir(config, "")
        return self._cache[paso]

    def _construir(self, config: dict, api_key: str) -> LLMClient:
        proveedor = config["proveedor"]
        # La del visitante gana; si no trajo, la que el servidor tenga para ESE
        # proveedor. Se copia el settings en vez de mutarlo: la configuracion del
        # proceso la comparten todas las demas peticiones.
        clave = api_key or self.clave_de(proveedor)
        settings = self.settings.model_copy(update={
            "llm_provider": proveedor,
            "anthropic_api_key": clave,
            "llm_api_key": clave,
        })
        return self._constructor(settings, config["modelo"], self.tracer)

    def invalidar(self) -> None:
        """Suelta los clientes cacheados para que el proximo se arme de nuevo."""
        for cliente in self._cache.values():
            cerrar = getattr(cliente, "close", None)
            if cerrar:
                try:
                    cerrar()
                except Exception:
                    logger.debug("No se pudo cerrar un cliente", exc_info=True)
        self._cache.clear()

    # ── Para el panel ───────────────────────────────────────────────────

    def catalogo(self) -> list[dict]:
        """Proveedores conocidos, cuales son gratis y cual tiene clave cargada."""
        return [
            {
                "id": pid,
                "nombre": info["nombre"],
                "gratis": info["gratis"],
                "modelos": info["modelos"],
                "base_url": base_url_de(pid),
                "tiene_clave": self._hay_clave(pid),
                # De donde se saca la clave y con que forma: sin esto, elegir un
                # proveedor deja al que lo eligio buscando en Google.
                "consola": info["consola"],
                "formato_clave": info["formato_clave"],
            }
            for pid, info in PROVEEDORES_SUGERIDOS.items()
        ]

    def todo_es_gratis(self, override: dict | None = None) -> bool:
        """Si los tres pasos corren en proveedores con free tier y hay clave.

        Es lo que decide si el modo demo puede correr el pipeline de verdad en
        vez de servir un informe guardado. «Demo» nunca quiso decir «viejo»:
        quiso decir «que no le cueste a nadie». Cuando correrlo es gratis, la
        respuesta honesta es correrlo.
        """
        config = self.con_override(override)
        proveedores = {cfg["proveedor"] for cfg in config.values()}
        if "anthropic" in proveedores:
            return False
        return all(
            PROVEEDORES_SUGERIDOS.get(p, {}).get("gratis") and self._hay_clave(p)
            for p in proveedores
        )

    def clave_de(self, proveedor: str) -> str:
        """La credencial del servidor para ese proveedor, si la hay.

        Se busca de lo especifico a lo general: la del proveedor, la generica, y
        para Anthropic su variable de siempre. Asi el dueno del deploy puede
        cargar las de free tier —que no arriesgan nada— y dejar las de pago
        afuera, para que cada uno traiga la suya.
        """
        por_proveedor = (self.settings.llm_api_keys or {}).get(proveedor, "")
        if por_proveedor:
            return por_proveedor
        if proveedor == "anthropic":
            return self.settings.anthropic_api_key
        return self.settings.llm_api_key

    def _hay_clave(self, proveedor: str) -> bool:
        return bool(self.clave_de(proveedor))
