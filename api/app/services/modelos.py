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
from ..llm.manager import LLMManager

logger = logging.getLogger(__name__)


class ModelosService:
    """Resuelve, cachea y renueva el cliente de cada paso."""

    def __init__(self, db: Database, settings: Settings, manager: LLMManager):
        self.db = db
        self.settings = settings
        # Este servicio sabe QUE se eligio; el manager sabe COMO se le habla.
        # Ninguna de las dos mitades necesita entender a la otra.
        self.manager = manager

    @property
    def tracer(self):
        return self.manager.tracer

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
        """El cliente de ese paso, segun la configuracion vigente."""
        return self._construir(self.vigente()[paso], api_key)

    def _construir(self, config: dict, api_key: str) -> LLMClient:
        return self.manager.cliente(config["proveedor"], config["modelo"], api_key)

    def invalidar(self) -> None:
        """Cambio la configuracion: los clientes cacheados ya no sirven."""
        self.manager.invalidar()

    def completar(
        self, paso: str, system: str, user: str,
        *, demo: bool = False, api_key: str = "", trace_id: str | None = None, **extra,
    ):
        """La respuesta del modelo que le toca a ESE paso, en ESE modo.

        Es lo unico que necesita saber quien arma un prompt: el nombre del paso.
        Que proveedor, que modelo y con que credencial se resuelve aca, y hablar
        con el es del manager. Asi el llamador no puede equivocarse de cliente,
        porque no tiene ninguno para equivocarse.
        """
        config = (self.config_demo() if demo else self.vigente())
        if config is None:
            config = self.vigente()
        cfg = config[paso]
        return self.manager.completar(
            cfg["proveedor"], cfg["modelo"], system, user,
            api_key=api_key, trace_id=trace_id, **extra,
        )

    # ── La unica fabrica ────────────────────────────────────────────────

    def servicio(
        self, *, demo: bool = False, api_key: str = "", override: dict | None = None,
    ):
        """Un `ResolutionService` para este modo. Devuelve None si demo no aplica.

        No recibe clientes: recibe el modo. El servicio pide por paso y esta
        clase traduce. Antes esto armaba tres clientes y se los entregaba, que
        es como el juez termino corriendo en el proveedor equivocado.
        """
        from .resolution import ResolutionService

        if demo and self.modelo_demo() is None:
            return None
        if override:
            self._override = override
        return ResolutionService(
            tracer=self.tracer, modelos=self, demo=demo, api_key=api_key,
        )

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

    def modelo_demo(self) -> dict | None:
        """El modelo del modo demo, o None si no hay ninguno configurado.

        «Demo» nunca quiso decir «viejo»: quiso decir «que se pueda evaluar el
        sistema sin cuenta propia». Con un modelo configurado aca el pipeline
        corre entero y el informe es de ahora; sin el, se sirven los informes
        guardados, que es el respaldo.

        Es una configuracion aparte de la de produccion a proposito: produccion
        es Claude con la clave del visitante —la configuracion documentada, la
        que se mide—, y demo es un modelo accesible con la clave del servidor.
        Son dos decisiones distintas y merecen dos lugares distintos.
        """
        proveedor = (getattr(self.settings, "demo_provider", "") or "").strip().lower()
        modelo = (getattr(self.settings, "demo_model", "") or "").strip()
        if not (proveedor and modelo and self._hay_clave(proveedor)):
            return None
        return {"proveedor": proveedor, "modelo": modelo}

    def config_demo(self) -> dict[str, dict] | None:
        """Los tres pasos sobre el modelo del modo demo."""
        elegido = self.modelo_demo()
        if elegido is None:
            return None
        vigente = self.vigente()
        return {
            paso: {**vigente[paso], **elegido, "es_demo": True}
            for paso in PASOS_DEL_PIPELINE
        }

    def clientes_demo(self) -> dict[str, LLMClient] | None:
        """Un cliente por paso con el modelo del modo demo, o None."""
        config = self.config_demo()
        if config is None:
            return None
        return {paso: self._construir(config[paso], "") for paso in PASOS_DEL_PIPELINE}

    def _hay_clave(self, proveedor: str) -> bool:
        return self.manager.hay_clave(proveedor)
