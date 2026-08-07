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
        settings = self.settings
        if api_key:
            # Copia con la clave del visitante: no se toca la configuracion del
            # proceso, que la comparten todas las demas peticiones.
            settings = self.settings.model_copy(
                update={"anthropic_api_key": api_key, "llm_api_key": api_key}
            )
        settings = settings.model_copy(update={"llm_provider": config["proveedor"]})
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

    def _hay_clave(self, proveedor: str) -> bool:
        if proveedor == "anthropic":
            return bool(self.settings.anthropic_api_key)
        return bool(self.settings.llm_api_key)
