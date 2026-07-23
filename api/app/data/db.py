import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from ..domain.enums import TransactionStatus

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _row_to_dict(self, row) -> dict | None:
        return dict(row) if row else None

    def _rows_to_list(self, rows) -> list[dict]:
        return [dict(r) for r in rows]

    # --- Transactions ---

    def get_transaction(self, txn_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM transactions WHERE id = ?", (txn_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def get_all_transactions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM transactions").fetchall()
            return self._rows_to_list(rows)

    def list_transactions_compact(self) -> list[dict]:
        """Compact listing for the test panel dropdown (no logs, no joined data)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, merchant, amount_usd, country, payment_method, "
                "fraud_score, channel, status FROM transactions ORDER BY id"
            ).fetchall()
            return self._rows_to_list(rows)

    # --- Logs ---

    def get_logs_for_transaction(self, tx_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM logs WHERE transaction_id = ? ORDER BY timestamp",
                (tx_id,),
            ).fetchall()
            return self._rows_to_list(rows)

    # --- Cases ---

    def get_all_cases(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM cases").fetchall()
            return self._rows_to_list(rows)

    # --- Client history ---

    def get_client_history(self, client_id: str) -> dict:
        with self._conn() as conn:
            txns = conn.execute(
                "SELECT * FROM transactions WHERE client_id = ?", (client_id,)
            ).fetchall()
            txns = self._rows_to_list(txns)

            cases = conn.execute(
                """SELECT c.* FROM cases c
                   JOIN transactions t ON c.transaction_id = t.id
                   WHERE t.client_id = ?""",
                (client_id,),
            ).fetchall()
            cases = self._rows_to_list(cases)

        total_transactions = len(txns)
        total_chargebacks = len(cases)
        rejected = sum(1 for t in txns if t.get("status") == TransactionStatus.RECHAZADA)
        countries = list({t["country"] for t in txns})
        methods = list({t["payment_method"] for t in txns})

        return {
            "client_id": client_id,
            "total_transactions": total_transactions,
            "total_chargebacks": total_chargebacks,
            "rejected_transactions": rejected,
            "countries_used": countries,
            "payment_methods_used": methods,
        }

    # --- Merchant stats ---

    def get_merchant_stats(self, merchant: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt, SUM(amount_usd) as vol, AVG(amount_usd) as avg FROM transactions WHERE merchant = ?",
                (merchant,),
            ).fetchone()
            total = row["cnt"] or 0
            volume = row["vol"] or 0.0
            avg = row["avg"] or 0.0

            cb_row = conn.execute(
                """SELECT COUNT(*) as cnt FROM cases c
                   JOIN transactions t ON c.transaction_id = t.id
                   WHERE t.merchant = ?""",
                (merchant,),
            ).fetchone()
            total_cbs = cb_row["cnt"] or 0

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
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM policies ORDER BY code").fetchall()
            return self._rows_to_list(rows)

    def get_policy(self, code: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM policies WHERE code = ?", (code,)
            ).fetchone()
            return self._row_to_dict(row)

    def create_policy_record(self, fields: dict) -> dict:
        """Build a policy dict with timestamps and persist it. Returns the full dict."""
        now = datetime.now(timezone.utc).isoformat()
        policy = {**fields, "created_at": now, "updated_at": now}
        self.upsert_policy(policy)
        return policy

    def merge_policy_update(self, existing: dict, updates: dict) -> dict:
        """Merge partial updates into an existing policy and persist. Returns the merged dict."""
        merged = {**existing, **updates, "updated_at": datetime.now(timezone.utc).isoformat()}
        self.upsert_policy(merged)
        return merged

    def upsert_policy(self, policy: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT created_at FROM policies WHERE code = ?", (policy["code"],)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                "INSERT OR REPLACE INTO policies VALUES (?,?,?,?,?,?,?)",
                (
                    policy["code"], policy["name"], policy["category"],
                    policy["description"], policy["reference"],
                    created_at, now,
                ),
            )
            conn.commit()

    def delete_policy(self, code: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM policies WHERE code = ?", (code,))
            conn.commit()
            return cursor.rowcount > 0

    # --- Feedback ---

    def save_feedback(self, feedback: dict) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO feedback
                   (transaction_id, analyst_decision, analyst_notes, final_outcome, judge_score, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    feedback["transaction_id"],
                    feedback["analyst_decision"],
                    feedback["analyst_notes"],
                    feedback["final_outcome"],
                    feedback["judge_score"],
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    # --- Analytics ---

    def get_dashboard_stats(self) -> dict:
        """Aggregated metrics for the analytics dashboard."""
        with self._conn() as conn:
            tx_count = conn.execute("SELECT COUNT(*) as cnt FROM transactions").fetchone()["cnt"]
            case_count = conn.execute("SELECT COUNT(*) as cnt FROM cases").fetchone()["cnt"]
            feedback_count = conn.execute("SELECT COUNT(*) as cnt FROM feedback").fetchone()["cnt"]

            avg_row = conn.execute("SELECT AVG(judge_score) as avg FROM feedback").fetchone()
            avg_judge_score = round(avg_row["avg"], 2) if avg_row["avg"] else 0.0

            auto_indexed = conn.execute(
                "SELECT COUNT(*) as cnt FROM feedback WHERE judge_score >= ?", (8.0,)
            ).fetchone()["cnt"]

            top_merchants = conn.execute(
                "SELECT t.merchant, COUNT(*) as cb_count "
                "FROM cases c JOIN transactions t ON c.transaction_id = t.id "
                "GROUP BY t.merchant ORDER BY cb_count DESC LIMIT 5"
            ).fetchall()

            by_country = conn.execute(
                "SELECT country, COUNT(*) as cnt FROM transactions GROUP BY country ORDER BY cnt DESC"
            ).fetchall()

            by_payment = conn.execute(
                "SELECT payment_method, COUNT(*) as cnt FROM transactions GROUP BY payment_method ORDER BY cnt DESC"
            ).fetchall()

        return {
            "total_transactions": tx_count,
            "total_cases": case_count,
            "total_feedback": feedback_count,
            "avg_judge_score": avg_judge_score,
            "auto_indexed_count": auto_indexed,
            "top_merchants_by_chargebacks": self._rows_to_list(top_merchants),
            "transactions_by_country": self._rows_to_list(by_country),
            "transactions_by_payment_method": self._rows_to_list(by_payment),
        }

    # --- Report Cache (idempotency) ---

    def ensure_report_cache_table(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS report_cache (
                    cache_key TEXT PRIMARY KEY,
                    html TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            conn.commit()

    def get_cached_report(self, cache_key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT html FROM report_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            return row["html"] if row else None

    def store_cached_report(self, cache_key: str, html: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO report_cache (cache_key, html, created_at)
                   VALUES (?, ?, ?)""",
                (cache_key, html, now),
            )
            conn.commit()
