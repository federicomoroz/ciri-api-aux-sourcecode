"""
Unit tests for rag/formatter.py — prompt formatting for policies and cases.
"""

from api.app.rag.formatter import format_cases_for_prompt, format_policies_for_prompt, _motivo_matches


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


class TestMotivoMatching:
    """Deterministic motivo synonym matching."""

    def test_exact_match(self):
        assert _motivo_matches("Cargo duplicado", "cargo duplicado")

    def test_synonym_match_doble_duplicado(self):
        assert _motivo_matches("Cargo duplicado", "cargo doble por timeout")

    def test_synonym_match_fraude_no_reconoce(self):
        assert _motivo_matches("No reconoce la compra", "compra no autorizada fraude")

    def test_no_match_different_motivos(self):
        assert not _motivo_matches("Cargo duplicado", "Producto defectuoso")

    def test_no_match_empty(self):
        assert not _motivo_matches("", "Cargo duplicado")

    def test_case_insensitive(self):
        assert _motivo_matches("CARGO DUPLICADO", "doble cobro en sistema")

    def test_observations_checked_in_format(self):
        """Cases with matching observations (not just motivo) are tagged."""
        cases = [
            {"case_id": "CB-001", "motivo": "Monto incorrecto", "score": 0.8,
             "resolution": "Cerrado", "resolution_days": 3,
             "merchant": "Rappi", "amount_usd": 50, "country": "COL",
             "observations": "Error en sistema — cargo doble por timeout"},
            {"case_id": "CB-002", "motivo": "Defecto", "score": 0.7,
             "resolution": "Reembolso", "resolution_days": 5,
             "merchant": "Amazon", "amount_usd": 100, "country": "MEX"},
        ]
        result = format_cases_for_prompt(cases, current_motivo="Cargo duplicado")
        # CB-001 should be tagged and listed first
        assert "[MOTIVO SIMILAR]" in result
        pos_cb001 = result.index("CB-001")
        pos_cb002 = result.index("CB-002")
        assert pos_cb001 < pos_cb002

    def test_no_motivo_no_tags(self):
        """Without current_motivo, no tags are added."""
        cases = [
            {"case_id": "CB-001", "motivo": "Cargo duplicado", "score": 0.8,
             "resolution": "R", "resolution_days": 1,
             "merchant": "X", "amount_usd": 10, "country": "A"},
        ]
        result = format_cases_for_prompt(cases, current_motivo=None)
        assert "[MOTIVO SIMILAR]" not in result
