import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime

from ..domain.constants import (
    DASHBOARD_TOP_N,
    JUDGE_AUTO_INDEX_THRESHOLD,
    POLICY_SEED_BLOQUEANTES,
    POLICY_SEED_SLA_DIAS,
    SQLITE_TIMEOUT_S,
)

logger = logging.getLogger(__name__)


def cache_key(transaction_id: str, cliente_vip: bool = False) -> str:
    """Build the idempotency cache key for a report."""
    return f"{transaction_id}|{cliente_vip}"


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=SQLITE_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        except sqlite3.Error as e:
            logger.error("SQLite error on %s: %s", self.db_path, e)
            raise
        finally:
            conn.close()

    # Casi todo lo que hace esta clase es una de estas tres cosas. Tenerlas
    # nombradas deja a cada metodo publico reducido a su consulta.

    def _consultar(self, sql: str, params: tuple = ()) -> list[dict]:
        """Filas como diccionarios."""
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _uno(self, sql: str, params: tuple = ()) -> dict | None:
        """La primera fila, o None."""
        with self._conn() as conn:
            fila = conn.execute(sql, params).fetchone()
            return dict(fila) if fila else None

    def _escribir(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Escritura con commit. Devuelve el cursor para lastrowid o rowcount."""
        with self._conn() as conn:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor

    # --- Transactions ---

    def get_transaction(self, txn_id: str) -> dict | None:
        return self._uno("SELECT * FROM transactions WHERE id = ?", (txn_id,))

    def get_all_transactions(self) -> list[dict]:
        return self._consultar("SELECT * FROM transactions")

    def list_transactions_compact(self) -> list[dict]:
        """Compact listing for the test panel dropdown (no logs, no joined data)."""
        return self._consultar(
            "SELECT id, merchant, amount_usd, country, payment_method, "
            "fraud_score, channel, status FROM transactions ORDER BY id"
        )

    # --- Logs ---

    def get_logs_for_transaction(self, tx_id: str) -> list[dict]:
        return self._consultar(
            "SELECT * FROM logs WHERE transaction_id = ? ORDER BY timestamp", (tx_id,),
        )

    # --- Cases ---

    def get_all_cases(self) -> list[dict]:
        return self._consultar("SELECT * FROM cases")

    # --- Client history ---

    def get_client_history(self, client_id: str) -> dict:
        """Raw client data: transactions + cases. Business logic (flags) in Analyzer."""
        return {
            "client_id": client_id,
            "transactions": self._consultar(
                "SELECT * FROM transactions WHERE client_id = ?", (client_id,),
            ),
            "cases": self._consultar(
                """SELECT c.* FROM cases c
                   JOIN transactions t ON c.transaction_id = t.id
                   WHERE t.client_id = ?""",
                (client_id,),
            ),
        }

    # --- Merchant stats ---

    def get_corpus_cb_ratio(self) -> float:
        """Contracargos sobre transacciones en todo el corpus.

        Es la referencia contra la que se juzga a cada comercio. Se consulta en
        vez de fijarse: si manana se cargan mas transacciones, la linea base se
        mueve sola y los flags siguen significando lo mismo.
        """
        totales = self._uno(
            "SELECT (SELECT COUNT(*) FROM cases) AS cbs, "
            "(SELECT COUNT(*) FROM transactions) AS txs"
        ) or {}
        txs = totales.get("txs") or 0
        return (totales.get("cbs") or 0) / txs if txs else 0.0

    def get_case_for_transaction(self, txn_id: str) -> dict | None:
        """El caso historico de esa transaccion, si existe.

        De aca salen las fechas reales de apertura y cierre del reclamo, que son
        las que el SLA tiene que medir. La fecha de la transaccion es cuando se
        compro, no cuando se abrio la disputa.
        """
        return self._uno(
            "SELECT * FROM cases WHERE transaction_id = ? ORDER BY open_date LIMIT 1",
            (txn_id,),
        )

    def get_merchant_stats(self, merchant: str) -> dict:
        totales = self._uno(
            "SELECT COUNT(*) as cnt, SUM(amount_usd) as vol, AVG(amount_usd) as avg "
            "FROM transactions WHERE merchant = ?",
            (merchant,),
        ) or {}
        total = totales.get("cnt") or 0
        volume = totales.get("vol") or 0.0
        avg = totales.get("avg") or 0.0

        contracargos = self._uno(
            """SELECT COUNT(*) as cnt FROM cases c
               JOIN transactions t ON c.transaction_id = t.id
               WHERE t.merchant = ?""",
            (merchant,),
        ) or {}
        total_cbs = contracargos.get("cnt") or 0

        cb_ratio = total_cbs / total if total > 0 else 0.0

        return {
            "merchant": merchant,
            "total_transactions": total,
            "total_chargebacks": total_cbs,
            "cb_ratio": round(cb_ratio, 4),
            "total_volume_usd": round(volume, 2),
            "avg_transaction_usd": round(avg, 2),
        }

    # --- Policies ---

    def get_all_policies(self) -> list[dict]:
        return self._consultar("SELECT * FROM policies ORDER BY code")

    def get_policy(self, code: str) -> dict | None:
        return self._uno("SELECT * FROM policies WHERE code = ?", (code,))

    def create_policy_record(self, fields: dict) -> dict:
        """Build a policy dict with timestamps and persist it. Returns the full dict."""
        now = datetime.now(UTC).isoformat()
        policy = {**fields, "created_at": now, "updated_at": now}
        self.upsert_policy(policy)
        return policy

    def merge_policy_update(self, existing: dict, updates: dict) -> dict:
        """Merge partial updates into an existing policy and persist. Returns the merged dict."""
        merged = {**existing, **updates, "updated_at": datetime.now(UTC).isoformat()}
        self.upsert_policy(merged)
        return merged

    def upsert_policy(self, policy: dict) -> None:
        """Reemplaza la politica conservando su fecha de creacion original."""
        ahora = datetime.now(UTC).isoformat()
        previa = self._uno(
            "SELECT created_at FROM policies WHERE code = ?", (policy["code"],),
        )
        # Columnas nombradas: con VALUES posicional, agregar una columna
        # desplaza los datos en silencio.
        self._escribir(
            "INSERT OR REPLACE INTO policies "
            "(code, name, category, description, reference, created_at, updated_at, "
            " puede_bloquear, sla_dias) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                policy["code"], policy["name"], policy["category"],
                policy["description"], policy["reference"],
                previa["created_at"] if previa else ahora, ahora,
                int(bool(policy.get("puede_bloquear", False))),
                policy.get("sla_dias"),
            ),
        )

    def ensure_policies_semantics(self) -> None:
        """Agrega las columnas de semantica a una base que ya existia.

        SQLite en el free tier de Render se recrea del Excel cuando falta, pero
        una base local con datos previos no tiene estas columnas y la app no
        puede exigir que se borre para actualizarse.
        """
        existentes = {c["name"] for c in self._consultar("PRAGMA table_info(policies)")}
        for columna, ddl in (
            ("puede_bloquear", "INTEGER NOT NULL DEFAULT 0"),
            ("sla_dias", "INTEGER"),
        ):
            if columna not in existentes:
                self._escribir(f"ALTER TABLE policies ADD COLUMN {columna} {ddl}")
                logger.info("policies: columna %s agregada", columna)

        if "puede_bloquear" not in existentes:
            marcadores = ",".join("?" * len(POLICY_SEED_BLOQUEANTES))
            self._escribir(
                f"UPDATE policies SET puede_bloquear = 1 WHERE code IN ({marcadores})",
                tuple(sorted(POLICY_SEED_BLOQUEANTES)),
            )
        if "sla_dias" not in existentes:
            for codigo, dias in POLICY_SEED_SLA_DIAS.items():
                self._escribir(
                    "UPDATE policies SET sla_dias = ? WHERE code = ?", (dias, codigo),
                )

    def delete_policy(self, code: str) -> bool:
        return self._escribir("DELETE FROM policies WHERE code = ?", (code,)).rowcount > 0

    # --- Feedback ---

    def save_feedback(self, feedback: dict) -> int:
        return self._escribir(
            """INSERT INTO feedback
               (transaction_id, analyst_decision, analyst_notes, final_outcome,
                judge_score, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                feedback["transaction_id"], feedback["analyst_decision"],
                feedback["analyst_notes"], feedback["final_outcome"],
                feedback["judge_score"], datetime.now(UTC).isoformat(),
            ),
        ).lastrowid

    # --- Analytics ---

    def _contar(self, sql: str, params: tuple = ()) -> int:
        """Una consulta de agregacion que devuelve un solo numero."""
        fila = self._uno(sql, params) or {}
        return next(iter(fila.values()), 0) or 0

    def get_dashboard_stats(self) -> dict:
        """Aggregated metrics for the analytics dashboard."""
        promedio = self._contar("SELECT AVG(judge_score) as avg FROM feedback")
        return {
            "total_transactions": self._contar("SELECT COUNT(*) as cnt FROM transactions"),
            "total_cases": self._contar("SELECT COUNT(*) as cnt FROM cases"),
            "total_feedback": self._contar("SELECT COUNT(*) as cnt FROM feedback"),
            "avg_judge_score": round(promedio, 2) if promedio else 0.0,
            "auto_indexed_count": self._contar(
                "SELECT COUNT(*) as cnt FROM feedback WHERE judge_score >= ?",
                (JUDGE_AUTO_INDEX_THRESHOLD,),
            ),
            "top_merchants_by_chargebacks": self._consultar(
                "SELECT t.merchant, COUNT(*) as cb_count "
                "FROM cases c JOIN transactions t ON c.transaction_id = t.id "
                "GROUP BY t.merchant ORDER BY cb_count DESC LIMIT ?",
                (DASHBOARD_TOP_N,),
            ),
            "transactions_by_country": self._consultar(
                "SELECT country, COUNT(*) as cnt FROM transactions "
                "GROUP BY country ORDER BY cnt DESC"
            ),
            "transactions_by_payment_method": self._consultar(
                "SELECT payment_method, COUNT(*) as cnt FROM transactions "
                "GROUP BY payment_method ORDER BY cnt DESC"
            ),
        }

    # --- Report Cache (idempotency) ---

    def ensure_report_cache_table(self) -> None:
        self._escribir(
            """CREATE TABLE IF NOT EXISTS report_cache (
                cache_key  TEXT PRIMARY KEY,
                html       TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )

    def get_cached_report(self, cache_key: str) -> str | None:
        fila = self._uno("SELECT html FROM report_cache WHERE cache_key = ?", (cache_key,))
        return fila["html"] if fila else None

    def store_cached_report(self, cache_key: str, html: str) -> None:
        self._escribir(
            """INSERT OR REPLACE INTO report_cache (cache_key, html, created_at)
               VALUES (?, ?, ?)""",
            (cache_key, html, datetime.now(UTC).isoformat()),
        )

    # --- Alerts (operational event log) ---

    def ensure_modelos_table(self) -> None:
        """La eleccion de modelo por paso del pipeline, editable sin deploy."""
        self._escribir(
            """CREATE TABLE IF NOT EXISTS configuracion_modelos (
                   paso TEXT PRIMARY KEY,
                   proveedor TEXT NOT NULL,
                   modelo TEXT NOT NULL,
                   actualizado TEXT NOT NULL
               )"""
        )

    def get_modelos(self) -> dict[str, dict]:
        """Lo elegido para cada paso. Vacio si nadie lo cambio nunca."""
        return {
            f["paso"]: {
                "proveedor": f["proveedor"],
                "modelo": f["modelo"],
                "actualizado": f["actualizado"],
            }
            for f in self._consultar("SELECT * FROM configuracion_modelos")
        }

    def save_modelo(self, paso: str, proveedor: str, modelo: str) -> None:
        self._escribir(
            "INSERT OR REPLACE INTO configuracion_modelos "
            "(paso, proveedor, modelo, actualizado) VALUES (?,?,?,?)",
            (paso, proveedor, modelo, datetime.now(UTC).isoformat()),
        )

    def reset_modelos(self) -> None:
        """Vuelve a los valores por defecto de `constants.py`."""
        self._escribir("DELETE FROM configuracion_modelos")

    def ensure_alerts_table(self) -> None:
        self._escribir(
            """CREATE TABLE IF NOT EXISTS alerts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type      TEXT NOT NULL,
                severity        TEXT NOT NULL DEFAULT 'ERROR',
                message         TEXT NOT NULL,
                source          TEXT NOT NULL DEFAULT '',
                transaction_id  TEXT,
                metadata_json   TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )"""
        )

    def save_alert(self, alert: dict) -> int:
        return self._escribir(
            """INSERT INTO alerts
               (event_type, severity, message, source, transaction_id,
                metadata_json, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                alert["event_type"], alert.get("severity", "ERROR"),
                alert["message"], alert.get("source", ""),
                alert.get("transaction_id"),
                json.dumps(alert.get("metadata", {}), ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        ).lastrowid

    def get_recent_alerts(self, limit: int = 50) -> list[dict]:
        return self._consultar(
            """SELECT id, event_type, severity, message, source,
                      transaction_id, created_at
               FROM alerts ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
