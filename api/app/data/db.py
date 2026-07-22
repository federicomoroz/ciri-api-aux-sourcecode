import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from ..domain.enums import TransactionStatus


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

    def get_cases_for_transaction(self, tx_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM cases WHERE transaction_id = ?", (tx_id,)
            ).fetchall()
            return self._rows_to_list(rows)

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
