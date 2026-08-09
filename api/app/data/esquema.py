"""El esquema de SQLite, entero. Se crea una vez, al arrancar.

**Un almacen, una definicion.** Esto estaba partido en tres modulos sin dueno:
`data/loader.py` creaba cinco tablas, este archivo otras tres mas las migraciones
sobre `policies`, y `observability/trazador_local.py` las tres suyas. La tabla
`policies` se creaba en un lado y se le agregaban columnas en otro, asi que
responder «que forma tiene la base» pedia leer tres archivos y saber cual corre
primero. Ahora esta todo aca y `loader.py` solo inserta filas.

Que esto no viva dentro de `Database` es deliberado: esa clase es un gateway de
acceso a datos —lee y escribe filas—, y crear tablas es la puesta a punto del
almacen. Ocurre una sola vez en la vida del proceso y la dispara el lifespan, no
un llamador cualquiera. Con las dos cosas juntas, cada consumidor de `Database`
recibia tambien la capacidad de recrear el esquema.

Pero se pide por la puerta de adelante: `db.ejecutar_ddl(...)` y
`db.columnas_de(...)`. Antes se llamaba a `db._escribir(...)` y `db._consultar(...)`
desde afuera de la clase — la responsabilidad estaba bien ubicada y el
acoplamiento seguia ahi, solo que invisible para el tipo.

**Las migraciones son aditivas y toleran una base que ya existe.** La SQLite del
free tier de Render se recrea del Excel cuando falta, pero una base local con
datos previos no puede exigir que se borre para actualizarse: por eso
`ALTER TABLE` sobre columnas ausentes en vez de un `CREATE` que las asuma.
"""

import logging

from ..domain.constants import POLICY_SEED_BLOQUEANTES, POLICY_SEED_SLA_DIAS
from ..domain.enums import Severity

logger = logging.getLogger(__name__)


# ── Los datos del dataset ───────────────────────────────────────────────────
# Se cargan del Excel. `loader.py` los inserta; la forma se declara aca.

TABLAS_DEL_DATASET: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS transactions (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        merchant TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        date TEXT NOT NULL,
        payment_method TEXT NOT NULL,
        country TEXT NOT NULL,
        channel TEXT NOT NULL,
        device TEXT NOT NULL,
        fraud_score INTEGER NOT NULL,
        status TEXT NOT NULL,
        notes TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS cases (
        case_id TEXT PRIMARY KEY,
        transaction_id TEXT NOT NULL,
        motivo TEXT NOT NULL,
        resolution TEXT NOT NULL,
        resolution_days INTEGER NOT NULL,
        analyst TEXT NOT NULL,
        observations TEXT,
        open_date TEXT NOT NULL,
        close_date TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS policies (
        code TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        reference TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        -- La semantica ejecutable de la politica, junto a su texto. Estaba en
        -- constants.py: se podia editar la descripcion por API pero no la
        -- capacidad de bloquear ni el plazo, que son las dos cosas que la
        -- politica realmente hace.
        puede_bloquear INTEGER NOT NULL DEFAULT 0,
        sla_dias INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        transaction_id TEXT NOT NULL,
        event TEXT NOT NULL,
        service TEXT NOT NULL,
        code TEXT NOT NULL,
        detail TEXT NOT NULL,
        severity TEXT NOT NULL
    )""",
)

# ── Lo que el sistema escribe mientras corre ────────────────────────────────

TABLAS_DE_RUNTIME: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT NOT NULL,
        analyst_decision TEXT NOT NULL,
        analyst_notes TEXT NOT NULL,
        final_outcome TEXT NOT NULL,
        judge_score REAL NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS report_cache (
        cache_key  TEXT PRIMARY KEY,
        html       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    # f-string para que la severidad por defecto salga del enum. El `{{}}` de
    # metadata_json es un objeto JSON vacio y va escapado a proposito: sin las
    # llaves dobles, el f-string lo interpola como cadena vacia y la columna
    # queda con un default que no es JSON valido.
    f"""CREATE TABLE IF NOT EXISTS alerts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type      TEXT NOT NULL,
        severity        TEXT NOT NULL DEFAULT '{Severity.ERROR}',
        message         TEXT NOT NULL,
        source          TEXT NOT NULL DEFAULT '',
        transaction_id  TEXT,
        metadata_json   TEXT NOT NULL DEFAULT '{{}}',
        created_at      TEXT NOT NULL
    )""",
    # La eleccion de modelo por paso del pipeline, editable sin deploy.
    """CREATE TABLE IF NOT EXISTS configuracion_modelos (
        paso TEXT PRIMARY KEY,
        proveedor TEXT NOT NULL,
        modelo TEXT NOT NULL,
        actualizado TEXT NOT NULL
    )""",
)

# ── Observabilidad local ────────────────────────────────────────────────────
# Las usa `TrazadorLocal` cuando no hay Langfuse configurado. Estaban declaradas
# adentro de ese modulo, que es el unico que las escribe — pero son parte de la
# forma de esta base igual, y tenerlas afuera hacia que «que tablas hay» no se
# pudiera contestar leyendo un solo archivo.

TABLAS_DE_TRAZAS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS trazas (
        trace_id   TEXT PRIMARY KEY,
        nombre     TEXT NOT NULL,
        metadata   TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS generaciones (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id   TEXT,
        nombre     TEXT NOT NULL,
        modelo     TEXT NOT NULL,
        tokens_in  INTEGER NOT NULL DEFAULT 0,
        tokens_out INTEGER NOT NULL DEFAULT 0,
        latency_ms REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS puntajes (
        trace_id   TEXT NOT NULL,
        nombre     TEXT NOT NULL,
        valor      REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (trace_id, nombre)
    )""",
)

# ── Indices ─────────────────────────────────────────────────────────────────
# Las columnas por las que filtra el pipeline. Declararlos documenta como se
# consulta la base.

INDICES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_logs_tx ON logs(transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_cases_tx ON cases(transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_tx_client ON transactions(client_id)",
    "CREATE INDEX IF NOT EXISTS idx_tx_merchant ON transactions(merchant)",
)

# Aparte de los de arriba porque `TrazadorLocal` prepara solo lo suyo: puede
# arrancar sobre una base recien creada, antes de que el seed haya cargado las
# tablas del dataset, y un indice sobre una tabla que no existe falla.
INDICES_DE_TRAZAS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_generaciones_trace ON generaciones(trace_id)",
)

# Lo que necesita el registro local de observabilidad, y nada mas.
OBSERVABILIDAD: tuple[str, ...] = TABLAS_DE_TRAZAS + INDICES_DE_TRAZAS

TODO: tuple[str, ...] = (
    TABLAS_DEL_DATASET + TABLAS_DE_RUNTIME + INDICES + OBSERVABILIDAD
)


def preparar(db) -> None:
    """Todo lo que el esquema necesita antes de la primera peticion.

    Lo llama el lifespan. Es idempotente: correrlo dos veces no hace nada la
    segunda, que es lo que permite que los tests lo invoquen sin ceremonia.
    """
    db.ejecutar_ddl(TODO)
    semantica_de_politicas(db)
    _avisar_si_esta_vacia(db)


def _avisar_si_esta_vacia(db) -> None:
    """Una base con el esquema puesto y sin datos no puede pasar por sana.

    Antes de consolidar el esquema, `preparar()` sobre una base sin sembrar
    explotaba en el `ALTER TABLE policies` —la tabla no existia— y el arranque
    fallaba ruidosamente. Ahora crea las tablas primero, asi que el mismo caso
    levanta una API que responde `/health: healthy` con cero politicas y cero
    transacciones: todas las busquedas devuelven vacio y el agente deriva todo a
    revision humana sin que nada explique por que.

    El seed corre antes que esto en el arranque normal (`dependencies`), asi que
    llegar aca con la base vacia significa que el Excel no se cargo.
    """
    try:
        politicas = len(db.get_all_policies())
        transacciones = len(db.get_all_transactions())
    except Exception:
        logger.warning("No se pudo comprobar si la base tiene datos", exc_info=True)
        return
    if not (politicas and transacciones):
        logger.error(
            "La base tiene el esquema pero no los datos (%d politicas, %d transacciones). "
            "El agente va a derivar todo a revision humana: sin politicas no hay veredictos "
            "que evaluar. Correr `python scripts/seed_data.py`.",
            politicas, transacciones,
        )


def semantica_de_politicas(db) -> None:
    """Agrega las columnas de semantica a una base que ya existia.

    SQLite en el free tier de Render se recrea del Excel cuando falta, pero una
    base local con datos previos no tiene estas columnas y la app no puede exigir
    que se borre para actualizarse.
    """
    existentes = db.columnas_de("policies")
    if not existentes:
        return

    nuevas = [
        (columna, ddl)
        for columna, ddl in (
            ("puede_bloquear", "INTEGER NOT NULL DEFAULT 0"),
            ("sla_dias", "INTEGER"),
        )
        if columna not in existentes
    ]
    if nuevas:
        db.ejecutar_ddl(tuple(
            f"ALTER TABLE policies ADD COLUMN {columna} {ddl}" for columna, ddl in nuevas
        ))
        for columna, _ in nuevas:
            logger.info("policies: columna %s agregada", columna)

    if "puede_bloquear" not in existentes:
        db.marcar_politicas_bloqueantes(POLICY_SEED_BLOQUEANTES)
    if "sla_dias" not in existentes:
        db.sembrar_plazos_de_politica(POLICY_SEED_SLA_DIAS)
