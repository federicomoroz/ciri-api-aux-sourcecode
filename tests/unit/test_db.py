"""
Unit tests for Database class — data access layer.

Covers:
- Transaction queries (get, list, compact)
- Log queries
- Client history
- Merchant stats
- Policy CRUD (create, merge_update, upsert, delete)
- Feedback storage
- Dashboard stats aggregation
- Report cache (ensure table, store, get)
"""

import pytest

from api.app.data.db import Database


@pytest.fixture
def db(in_memory_db_path):
    return Database(in_memory_db_path)


# ---- Transactions ----

class TestTransactionQueries:

    def test_get_existing_transaction(self, db):
        tx = db.get_transaction("TXN-00051")
        assert tx is not None
        assert tx["id"] == "TXN-00051"
        assert tx["merchant"] == "Airbnb"

    def test_get_missing_transaction_returns_none(self, db):
        assert db.get_transaction("TXN-99999") is None

    def test_get_all_transactions(self, db):
        txns = db.get_all_transactions()
        assert len(txns) == 2  # fixture has 2 transactions
        ids = {t["id"] for t in txns}
        assert "TXN-00051" in ids
        assert "TXN-00042" in ids

    def test_list_transactions_compact(self, db):
        compact = db.list_transactions_compact()
        assert len(compact) == 2
        # Compact listing should include key fields
        first = compact[0]
        assert "id" in first
        assert "merchant" in first
        assert "fraud_score" in first
        # Compact should NOT include verbose fields like 'device', 'notes'
        assert "device" not in first
        assert "notes" not in first


# ---- Logs ----

class TestLogQueries:

    def test_get_logs_for_existing_transaction(self, db):
        """Fixture has no logs seeded; should return empty list."""
        logs = db.get_logs_for_transaction("TXN-00051")
        assert isinstance(logs, list)

    def test_get_logs_for_missing_transaction(self, db):
        logs = db.get_logs_for_transaction("TXN-NONEXISTENT")
        assert logs == []


# ---- Client History ----

class TestClientHistory:

    def test_known_client(self, db):
        history = db.get_client_history("CLI-0003")
        assert history["client_id"] == "CLI-0003"
        assert len(history["transactions"]) >= 1
        assert "cases" in history

    def test_unknown_client_returns_empty(self, db):
        history = db.get_client_history("CLI-NONEXISTENT")
        assert history["client_id"] == "CLI-NONEXISTENT"
        assert history["transactions"] == []
        assert history["cases"] == []


# ---- Merchant Stats ----

class TestMerchantStats:

    def test_known_merchant(self, db):
        stats = db.get_merchant_stats("Airbnb")
        assert stats["merchant"] == "Airbnb"
        assert stats["total_transactions"] >= 1
        assert "cb_ratio" in stats
        assert "total_volume_usd" in stats
        assert "avg_transaction_usd" in stats

    def test_unknown_merchant_returns_zeros(self, db):
        stats = db.get_merchant_stats("NonExistentMerchant")
        assert stats["total_transactions"] == 0
        assert stats["cb_ratio"] == 0.0
        assert stats["total_volume_usd"] == 0.0


# ---- Policy CRUD ----

class TestPolicyCRUD:

    def test_get_all_policies(self, db):
        policies = db.get_all_policies()
        assert len(policies) == 3  # fixture has 3 policies

    def test_get_policy_by_code(self, db):
        policy = db.get_policy("POL-FRD-001")
        assert policy is not None
        assert policy["code"] == "POL-FRD-001"

    def test_get_policy_not_found(self, db):
        assert db.get_policy("POL-MISSING") is None

    def test_create_policy_record(self, db):
        policy = db.create_policy_record({
            "code": "POL-NEW-001",
            "name": "New Policy",
            "category": "TEST",
            "description": "Created by test",
            "reference": "Test Ref",
        })
        assert policy["code"] == "POL-NEW-001"
        assert "created_at" in policy
        assert "updated_at" in policy
        # Verify it's persisted
        fetched = db.get_policy("POL-NEW-001")
        assert fetched is not None
        assert fetched["name"] == "New Policy"

    def test_merge_policy_update(self, db):
        existing = db.get_policy("POL-FRD-001")
        merged = db.merge_policy_update(existing, {"description": "Updated description"})
        assert merged["description"] == "Updated description"
        assert merged["code"] == "POL-FRD-001"  # code unchanged
        # Verify persisted
        fetched = db.get_policy("POL-FRD-001")
        assert fetched["description"] == "Updated description"

    def test_delete_policy_existing(self, db):
        result = db.delete_policy("POL-SLA-002")
        assert result is True
        assert db.get_policy("POL-SLA-002") is None

    def test_delete_policy_nonexistent(self, db):
        result = db.delete_policy("POL-NONEXISTENT")
        assert result is False

    def test_upsert_preserves_created_at(self, db):
        """Upserting an existing policy should preserve original created_at."""
        original = db.get_policy("POL-FRD-001")
        original_created = original["created_at"]
        db.upsert_policy({
            "code": "POL-FRD-001",
            "name": "Updated",
            "category": "FRAUDE",
            "description": "Updated",
            "reference": "Updated",
        })
        updated = db.get_policy("POL-FRD-001")
        assert updated["created_at"] == original_created
        assert updated["name"] == "Updated"


# ---- Feedback ----

class TestFeedback:

    def test_save_feedback_returns_id(self, db):
        feedback_id = db.save_feedback({
            "transaction_id": "TXN-00051",
            "analyst_decision": "APPROVED",
            "analyst_notes": "Verified.",
            "final_outcome": "REJECT",
            "judge_score": 9.0,
        })
        assert isinstance(feedback_id, int)
        assert feedback_id > 0

    def test_save_multiple_feedback(self, db):
        id1 = db.save_feedback({
            "transaction_id": "TXN-00051",
            "analyst_decision": "APPROVED",
            "analyst_notes": "First review",
            "final_outcome": "REJECT",
            "judge_score": 8.0,
        })
        id2 = db.save_feedback({
            "transaction_id": "TXN-00042",
            "analyst_decision": "REJECTED",
            "analyst_notes": "Bad case",
            "final_outcome": "APPROVE",
            "judge_score": 4.0,
        })
        assert id2 > id1


# ---- Dashboard Stats ----

class TestDashboardStats:

    def test_returns_all_keys(self, db):
        stats = db.get_dashboard_stats()
        expected_keys = {
            "total_transactions", "total_cases", "total_feedback",
            "avg_judge_score", "auto_indexed_count",
            "top_merchants_by_chargebacks", "transactions_by_country",
            "transactions_by_payment_method",
        }
        assert expected_keys.issubset(stats.keys())

    def test_counts_match_fixture_data(self, db):
        stats = db.get_dashboard_stats()
        assert stats["total_transactions"] == 2
        assert stats["total_cases"] == 1
        assert stats["total_feedback"] == 0  # no feedback in fixture
        assert stats["avg_judge_score"] == 0.0  # no feedback yet

    def test_after_feedback_updates_stats(self, db):
        db.save_feedback({
            "transaction_id": "TXN-00051",
            "analyst_decision": "APPROVED",
            "analyst_notes": "Verified correctly",
            "final_outcome": "REJECT",
            "judge_score": 9.0,
        })
        stats = db.get_dashboard_stats()
        assert stats["total_feedback"] == 1
        assert stats["avg_judge_score"] == 9.0
        assert stats["auto_indexed_count"] == 1  # 9.0 >= 8.0


# ---- Report Cache ----

class TestReportCache:

    def test_ensure_table_idempotent(self, db):
        """Calling ensure_report_cache_table twice should not error."""
        db.ensure_report_cache_table()
        db.ensure_report_cache_table()

    def test_store_and_retrieve(self, db):
        db.ensure_report_cache_table()
        db.store_cached_report("TXN-00051|False", "<html>Test</html>")
        html = db.get_cached_report("TXN-00051|False")
        assert html == "<html>Test</html>"

    def test_cache_miss(self, db):
        db.ensure_report_cache_table()
        assert db.get_cached_report("NONEXISTENT") is None

    def test_store_overwrites_existing(self, db):
        db.ensure_report_cache_table()
        db.store_cached_report("key1", "<html>V1</html>")
        db.store_cached_report("key1", "<html>V2</html>")
        assert db.get_cached_report("key1") == "<html>V2</html>"
