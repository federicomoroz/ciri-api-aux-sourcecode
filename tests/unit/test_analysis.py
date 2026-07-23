"""
Unit tests for the deterministic Analyzer module.
Uses in-memory SQLite — no Qdrant or LLM required.
"""

from datetime import date, timedelta

import pytest

from api.app.analysis.analyzer import Analyzer
from api.app.data.db import Database


@pytest.fixture
def db(in_memory_db_path):
    return Database(in_memory_db_path)


@pytest.fixture
def analyzer(db):
    return Analyzer(db)


class TestSLACheck:

    def test_latam_standard_10_days(self, analyzer):
        """Standard LATAM SLA: 10 business days."""
        recent_date = (date.today() - timedelta(days=5)).isoformat()
        result = analyzer.check_sla(recent_date, "ARG", cliente_vip=False)
        assert result["sla_type"] == "standard"
        assert result["sla_limit_days"] == 10
        assert result["within_sla"] is True
        assert result["compensation_applicable"] is False

    def test_non_latam_extended_15_days(self, analyzer):
        """Non-LATAM extended SLA: 15 business days."""
        recent_date = (date.today() - timedelta(days=12)).isoformat()
        result = analyzer.check_sla(recent_date, "USA", cliente_vip=False)
        assert result["sla_type"] == "extended"
        assert result["sla_limit_days"] == 15
        assert result["within_sla"] is True  # 12 <= 15

    def test_vip_5_days(self, analyzer):
        """VIP client SLA: 5 business days."""
        recent_date = (date.today() - timedelta(days=3)).isoformat()
        result = analyzer.check_sla(recent_date, "MEX", cliente_vip=True)
        assert result["sla_type"] == "vip"
        assert result["sla_limit_days"] == 5
        assert result["within_sla"] is True

    def test_sla_breach_triggers_compensation(self, analyzer):
        """Exceeded SLA should flag compensation_applicable."""
        old_date = (date.today() - timedelta(days=20)).isoformat()
        result = analyzer.check_sla(old_date, "BRA", cliente_vip=False)
        assert result["within_sla"] is False
        assert result["compensation_applicable"] is True

    def test_vip_breach_before_standard(self, analyzer):
        """VIP SLA is stricter: 6 days should breach VIP but not standard."""
        date_6_days_ago = (date.today() - timedelta(days=6)).isoformat()

        vip_result = analyzer.check_sla(date_6_days_ago, "ARG", cliente_vip=True)
        standard_result = analyzer.check_sla(date_6_days_ago, "ARG", cliente_vip=False)

        assert vip_result["within_sla"] is False   # 6 > 5
        assert standard_result["within_sla"] is True  # 6 <= 10


class TestErrorPatterns:

    def test_systematic_merchant_timeout(self, analyzer):
        """MERCHANT_NO_RESPONSE x2 should detect systematic_merchant_timeout."""
        logs = [
            {"severity": "ERROR", "event": "MERCHANT_NO_RESPONSE", "detail": "timeout", "timestamp": "2024-01-01 10:00:00", "code": "408"},
            {"severity": "ERROR", "event": "MERCHANT_NO_RESPONSE", "detail": "timeout again", "timestamp": "2024-01-01 10:01:00", "code": "408"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "systematic_merchant_timeout" in result["patterns"]

    def test_no_timeout_pattern_with_single_occurrence(self, analyzer):
        """Single MERCHANT_NO_RESPONSE should NOT trigger systematic pattern."""
        logs = [
            {"severity": "WARN", "event": "MERCHANT_NO_RESPONSE", "detail": "once", "timestamp": "2024-01-01 10:00:00", "code": "408"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "systematic_merchant_timeout" not in result["patterns"]

    def test_fraud_block_pattern(self, analyzer):
        """FRAUD_ALERT + AUTH_DECLINED should detect blocked_for_fraud."""
        logs = [
            {"severity": "WARN", "event": "FRAUD_ALERT", "detail": "score low", "timestamp": "2024-01-01", "code": "200"},
            {"severity": "ERROR", "event": "AUTH_DECLINED", "detail": "blocked", "timestamp": "2024-01-01", "code": "402"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "blocked_for_fraud" in result["patterns"]

    def test_severity_counts(self, analyzer):
        """Severity counts should be accurate."""
        logs = [
            {"severity": "ERROR", "event": "WEBHOOK_FAILED", "detail": "", "timestamp": "", "code": "500"},
            {"severity": "ERROR", "event": "WEBHOOK_FAILED", "detail": "", "timestamp": "", "code": "500"},
            {"severity": "WARN", "event": "TIMEOUT_RETRY", "detail": "", "timestamp": "", "code": "408"},
            {"severity": "INFO", "event": "AUTH_REQUEST", "detail": "", "timestamp": "", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert result["severity_counts"]["ERROR"] == 2
        assert result["severity_counts"]["WARN"] == 1
        assert result["severity_counts"]["INFO"] == 1

    def test_empty_logs(self, analyzer):
        """Empty logs should return empty result."""
        result = analyzer.detect_error_patterns([])
        assert result["patterns"] == []
        assert result["severity_counts"] == {"ERROR": 0, "WARN": 0, "INFO": 0}

    def test_duplicate_charge_pattern(self, analyzer):
        """DOUBLE_CHARGE_DETECT should detect duplicate_charge."""
        logs = [
            {"severity": "ERROR", "event": "DOUBLE_CHARGE_DETECT", "detail": "duplicate", "timestamp": "2024-01-01", "code": "409"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "duplicate_charge" in result["patterns"]

    def test_sla_violation_pattern(self, analyzer):
        """SLA_BREACH should detect sla_violation."""
        logs = [
            {"severity": "WARN", "event": "SLA_BREACH", "detail": "SLA exceeded", "timestamp": "2024-01-01", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "sla_violation" in result["patterns"]

    def test_integration_failure_pattern(self, analyzer):
        """WEBHOOK_FAILED should detect integration_failure."""
        logs = [
            {"severity": "ERROR", "event": "WEBHOOK_FAILED", "detail": "500 error", "timestamp": "2024-01-01", "code": "500"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "integration_failure" in result["patterns"]

    def test_session_interrupted_payment_pattern(self, analyzer):
        """SESSION_EXPIRED + PAYMENT_INITIATED should detect session_interrupted_payment."""
        logs = [
            {"severity": "INFO", "event": "PAYMENT_INITIATED", "detail": "starting", "timestamp": "2024-01-01 10:00:00", "code": "200"},
            {"severity": "WARN", "event": "SESSION_EXPIRED", "detail": "session timeout", "timestamp": "2024-01-01 10:05:00", "code": "401"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "session_interrupted_payment" in result["patterns"]

    def test_geographic_anomaly_pattern(self, analyzer):
        """GEO_ANOMALY should detect geographic_anomaly."""
        logs = [
            {"severity": "WARN", "event": "GEO_ANOMALY", "detail": "unusual location", "timestamp": "2024-01-01", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "geographic_anomaly" in result["patterns"]

    def test_connectivity_issue_pattern(self, analyzer):
        """TIMEOUT_RETRY should detect connectivity_issue."""
        logs = [
            {"severity": "WARN", "event": "TIMEOUT_RETRY", "detail": "retrying", "timestamp": "2024-01-01", "code": "408"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "connectivity_issue" in result["patterns"]

    def test_multiple_patterns_detected(self, analyzer):
        """Multiple patterns in same log set should all be detected."""
        logs = [
            {"severity": "WARN", "event": "FRAUD_ALERT", "detail": "score low", "timestamp": "2024-01-01", "code": "200"},
            {"severity": "ERROR", "event": "AUTH_DECLINED", "detail": "blocked", "timestamp": "2024-01-01", "code": "402"},
            {"severity": "ERROR", "event": "DOUBLE_CHARGE_DETECT", "detail": "duplicate", "timestamp": "2024-01-01", "code": "409"},
            {"severity": "WARN", "event": "GEO_ANOMALY", "detail": "location", "timestamp": "2024-01-01", "code": "200"},
        ]
        result = analyzer.detect_error_patterns(logs)
        assert "blocked_for_fraud" in result["patterns"]
        assert "duplicate_charge" in result["patterns"]
        assert "geographic_anomaly" in result["patterns"]


class TestMerchantRisk:

    def test_merchant_risk_returns_dict(self, analyzer):
        """merchant_risk_profile should return a dict with required keys."""
        result = analyzer.merchant_risk_profile("Airbnb")
        assert "merchant" in result
        assert "cb_ratio" in result
        assert "total_transactions" in result
        assert "flags" in result
        assert "is_strategic" in result

    def test_unknown_merchant_returns_zeros(self, analyzer):
        """Unknown merchant should return zero counts."""
        result = analyzer.merchant_risk_profile("NonExistentMerchant999")
        assert result["total_transactions"] == 0
        assert result["cb_ratio"] == 0.0


class TestClientFlags:

    def test_client_history_returns_dict(self, analyzer):
        """client_flags should return a dict with required keys."""
        result = analyzer.client_flags("CLI-0003")
        assert "client_id" in result
        assert "total_transactions" in result
        assert "total_chargebacks" in result
        assert "flags" in result

    def test_unknown_client_returns_empty(self, analyzer):
        """Unknown client should return zero counts."""
        result = analyzer.client_flags("CLI-9999")
        assert result["total_transactions"] == 0
