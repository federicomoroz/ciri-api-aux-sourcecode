"""
Unit tests for rag/formatter.py — prompt formatting for policies and cases.
"""

from api.app.rag.formatter import format_cases_for_prompt, format_policies_for_prompt


class TestFormatPoliciesForPrompt:

    def test_empty_returns_placeholder(self):
        result = format_policies_for_prompt([])
        assert "No se encontraron" in result

    def test_single_policy_has_all_fields(self):
        policies = [{
            "code": "POL-FRD-001",
            "category": "FRAUDE",
            "name": "Score minimo",
            "description": "Score < 30 = rechazo.",
            "reference": "Manual v3.2",
            "score": 0.95,
        }]
        result = format_policies_for_prompt(policies)
        assert "POL-FRD-001" in result
        assert "FRAUDE" in result
        assert "Score minimo" in result
        assert "95%" in result
        assert "Politica 1" in result

    def test_multiple_policies_numbered(self):
        policies = [
            {"code": "POL-001", "name": "P1", "category": "C1", "description": "D1", "reference": "R1", "score": 0.9},
            {"code": "POL-002", "name": "P2", "category": "C2", "description": "D2", "reference": "R2", "score": 0.8},
        ]
        result = format_policies_for_prompt(policies)
        assert "Politica 1" in result
        assert "Politica 2" in result

    def test_missing_score_defaults_to_zero(self):
        policies = [{"code": "POL-001", "name": "P1", "category": "C1", "description": "D1", "reference": "R1"}]
        result = format_policies_for_prompt(policies)
        assert "0%" in result

    def test_missing_fields_show_na(self):
        policies = [{}]
        result = format_policies_for_prompt(policies)
        assert "N/A" in result


class TestFormatCasesForPrompt:

    def test_empty_returns_placeholder(self):
        result = format_cases_for_prompt([])
        assert "No se encontraron" in result

    def test_single_case_has_all_fields(self):
        cases = [{
            "case_id": "CB-001",
            "motivo": "Fraude",
            "merchant": "Amazon",
            "amount_usd": 150.00,
            "country": "MEX",
            "resolution": "Reembolso",
            "resolution_days": 5,
            "score": 0.85,
            "observations": "Caso verificado",
        }]
        result = format_cases_for_prompt(cases)
        assert "CB-001" in result
        assert "Fraude" in result
        assert "Amazon" in result
        assert "150.00" in result
        assert "Reembolso" in result
        assert "85%" in result
        assert "Caso verificado" in result

    def test_case_without_observations(self):
        cases = [{
            "case_id": "CB-002",
            "motivo": "Test",
            "merchant": "X",
            "amount_usd": 10.0,
            "country": "ARG",
            "resolution": "R",
            "resolution_days": 1,
            "score": 0.5,
        }]
        result = format_cases_for_prompt(cases)
        assert "Observaciones" not in result

    def test_multiple_cases_numbered(self):
        cases = [
            {"case_id": "CB-001", "motivo": "M1", "score": 0.9, "resolution": "R1", "resolution_days": 1,
             "merchant": "X", "amount_usd": 10, "country": "A"},
            {"case_id": "CB-002", "motivo": "M2", "score": 0.8, "resolution": "R2", "resolution_days": 2,
             "merchant": "Y", "amount_usd": 20, "country": "B"},
        ]
        result = format_cases_for_prompt(cases)
        assert "Precedente 1" in result
        assert "Precedente 2" in result
