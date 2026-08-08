"""Crear y migrar el esquema. Se corre una vez, al arrancar.

Estaba dentro de `Database`, que es un gateway de acceso a datos: leer y escribir
filas. Crear tablas, agregar columnas a una base que ya existia y sembrar valores
iniciales no es acceso a datos — es la puesta a punto del almacen, ocurre una sola
vez en la vida del proceso y la dispara el lifespan, no un llamador cualquiera.

Con las dos cosas en la misma clase, cada consumidor de `Database` recibia
tambien la capacidad de recrear el esquema. Ninguno la usaba, pero estaba ahi.

**Las migraciones son aditivas y toleran una base que ya existe.** La SQLite del
free tier de Render se recrea del Excel cuando falta, pero una base local con
datos previos no puede exigir que se borre para actualizarse: por eso
`ALTER TABLE` sobre columnas ausentes en vez de un `CREATE` que las asuma.
"""

import logging

from ..domain.constants import POLICY_SEED_BLOQUEANTES, POLICY_SEED_SLA_DIAS
from ..domain.enums import Severity

logger = logging.getLogger(__name__)


def preparar(db) -> None:
    """Todo lo que el esquema necesita antes de la primera peticion.

    Lo llama el lifespan. Es idempotente: correrlo dos veces no hace nada la
    segunda, que es lo que permite que los tests lo invoquen sin ceremonia.
    """
    tabla_de_informes(db)
    tabla_de_alertas(db)
    tabla_de_modelos(db)
    semantica_de_politicas(db)


def tabla_de_informes(db) -> None:
    db._escribir(
        """CREATE TABLE IF NOT EXISTS report_cache (
            cache_key  TEXT PRIMARY KEY,
            html       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )


def tabla_de_alertas(db) -> None:
    # f-string para que la severidad por defecto salga del enum. El `{{}}` de
    # metadata_json es un objeto JSON vacio y va escapado a proposito: sin las
    # llaves dobles, el f-string lo interpola como cadena vacia y la columna
    # queda con un default que no es JSON valido.
    db._escribir(
        f"""CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT NOT NULL,
            severity        TEXT NOT NULL DEFAULT '{Severity.ERROR}',
            message         TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT '',
            transaction_id  TEXT,
            metadata_json   TEXT NOT NULL DEFAULT '{{}}',
            created_at      TEXT NOT NULL
        )"""
    )


def tabla_de_modelos(db) -> None:
    """La eleccion de modelo por paso del pipeline, editable sin deploy."""
    db._escribir(
        """CREATE TABLE IF NOT EXISTS configuracion_modelos (
               paso TEXT PRIMARY KEY,
               proveedor TEXT NOT NULL,
               modelo TEXT NOT NULL,
               actualizado TEXT NOT NULL
           )"""
    )


def semantica_de_politicas(db) -> None:
    """Agrega las columnas de semantica a una base que ya existia.

    SQLite en el free tier de Render se recrea del Excel cuando falta, pero
    una base local con datos previos no tiene estas columnas y la app no
    puede exigir que se borre para actualizarse.
    """
    existentes = {c["name"] for c in db._consultar("PRAGMA table_info(policies)")}
    for columna, ddl in (
        ("puede_bloquear", "INTEGER NOT NULL DEFAULT 0"),
        ("sla_dias", "INTEGER"),
    ):
        if columna not in existentes:
            db._escribir(f"ALTER TABLE policies ADD COLUMN {columna} {ddl}")
            logger.info("policies: columna %s agregada", columna)

    if "puede_bloquear" not in existentes:
        marcadores = ",".join("?" * len(POLICY_SEED_BLOQUEANTES))
        db._escribir(
            f"UPDATE policies SET puede_bloquear = 1 WHERE code IN ({marcadores})",
            tuple(sorted(POLICY_SEED_BLOQUEANTES)),
        )
    if "sla_dias" not in existentes:
        for codigo, dias in POLICY_SEED_SLA_DIAS.items():
            db._escribir(
                "UPDATE policies SET sla_dias = ? WHERE code = ?", (dias, codigo),
            )
