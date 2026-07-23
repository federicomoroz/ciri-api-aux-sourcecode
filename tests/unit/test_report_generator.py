"""
Unit tests for ReportGenerator — Jinja2 HTML rendering.
"""

import pytest

from api.app.reports.generator import ReportGenerator


@pytest.fixture
def generator():
    return ReportGenerator()


@pytest.fixture
def minimal_report_data():
    """Minimal data required to render a report."""
    return {
        "transaction": {
            "id": "TXN-00051", "client_id": "CLI-0003", "merchant": "Airbnb",
            "amount_usd": 2095.90, "date": "2024-09-23", "payment_method": "Cripto",
            "country": "COL", "channel": "POS", "device": "Firefox/Mac",
            "fraud_score": 8, "status": "Contracargo iniciado", "notes": None,
        },
        "resolution": {
            "transaction_id": "TXN-00051", "recommended_action": "REJECT",
            "confidence": 0.99, "justification": "BLOCKER cripto",
            "policy_verdicts": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER",
                                 "reasoning": "Cripto", "requires_human_review": False}],
            "precedent_summary": "", "log_summary": "", "risk_level": "BLOCKER",
            "compensation_applicable": False, "compensation_amount_usd": 0.0,
            "next_steps": ["Notificar al cliente"], "requires_hitl": False, "hitl_reason": None,
        },
        "judge_evaluation": {
            "overall_score": 9.2,
            "criteria": {"policy_consistency": 10.0, "justification_quality": 9.0,
                         "precedent_usage": 8.0, "risk_assessment": 9.5, "actionability": 9.5},
            "approved": True, "strengths": ["Correcto"], "weaknesses": [],
        },
        "agent_analysis": "BLOCKER detectado.",
        "merchant_risk": {"merchant": "Airbnb", "cb_ratio": 0.02, "total_transactions": 10,
                          "total_chargebacks": 2, "total_volume_usd": 5000, "avg_transaction_usd": 500,
                          "flags": [], "is_strategic": False},
        "client_profile": {"client_id": "CLI-0003", "total_transactions": 5, "total_chargebacks": 1,
                           "rejected_transactions": 0, "countries_used": ["COL"],
                           "payment_methods_used": ["Cripto"], "flags": []},
        "logs": [],
        "policies_evaluated": [{"policy_code": "POL-EXC-003", "verdict": "BLOCKER",
                                "reasoning": "Cripto", "requires_human_review": False}],
        "similar_cases": [],
        "hitl_decision": None,
        "cache_hit": False,
        "guardrail_warnings": [],
    }


class TestReportGenerator:

    def test_render_returns_html(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert isinstance(html, str)
        assert len(html) > 100

    def test_html_contains_transaction_id(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "TXN-00051" in html

    def test_html_contains_risk_level(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "BLOCKER" in html

    def test_html_contains_merchant(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "Airbnb" in html

    def test_html_contains_judge_score(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "9.2" in html

    def test_html_contains_generation_timestamp(self, generator, minimal_report_data):
        html = generator.render(minimal_report_data)
        assert "UTC" in html

    def test_autoescape_prevents_xss(self, generator, minimal_report_data):
        """Jinja2 autoescape should escape HTML in user-provided data."""
        minimal_report_data["agent_analysis"] = '<script>alert("xss")</script>'
        html = generator.render(minimal_report_data)
        # Jinja2 autoescape should prevent raw script tags
        assert '<script>alert' not in html

    def test_render_with_guardrail_warnings(self, generator, minimal_report_data):
        minimal_report_data["guardrail_warnings"] = [
            "GUARDRAIL: APPROVE con BLOCKER activo"
        ]
        html = generator.render(minimal_report_data)
        assert "GUARDRAIL" in html

    def test_render_with_hitl_decision(self, generator, minimal_report_data):
        minimal_report_data["hitl_decision"] = {
            "decision": "APPROVED",
            "notes": "Analyst approved after review",
        }
        html = generator.render(minimal_report_data)
        assert isinstance(html, str)
