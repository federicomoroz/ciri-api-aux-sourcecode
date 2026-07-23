"""
Edge-case tests for guardrails and ResolutionService._validate_resolution.

Covers scenarios not in test_guardrails.py:
- Multiple BLOCKERs
- Compensation at exactly 110% boundary
- Zero transaction amount
- Empty policy_verdicts
- Confidence exactly at 0.95 threshold
- Combined guardrails (APPROVE+BLOCKER + excessive compensation)
"""

import pytest

from api.app.services.resolution import ResolutionService


class TestGuardrailEdgeCases:

    def test_multiple_blockers_still_corrects(self):
        """APPROVE with 2+ BLOCKERs should still auto-correct."""
        resolution = {
            "recommended_action": "APPROVE",
            "risk_level": "LOW",
            "requires_hitl": True,
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
                {"policy_code": "POL-EXC-005", "verdict": "BLOCKER", "reasoning": "Sanction"},
            ],
        }
        tx = {"amount_usd": 500.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert len(warnings) == 1
        assert resolution["recommended_action"] == "REJECT"
        assert resolution["risk_level"] == "BLOCKER"

    def test_compensation_at_exact_boundary(self):
        """Compensation at exactly 110% should NOT trigger warning (> not >=)."""
        resolution = {
            "recommended_action": "APPROVE",
            "compensation_amount_usd": 110.0,
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Compensacion" in w for w in warnings)

    def test_compensation_just_over_boundary(self):
        """Compensation at 110.01% should trigger warning."""
        resolution = {
            "recommended_action": "APPROVE",
            "compensation_amount_usd": 110.01,
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert any("Compensacion" in w for w in warnings)

    def test_zero_amount_no_compensation_warning(self):
        """Zero transaction amount should not trigger compensation warning."""
        resolution = {
            "recommended_action": "APPROVE",
            "compensation_amount_usd": 50.0,
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Compensacion" in w for w in warnings)

    def test_empty_policy_verdicts_no_blocker_warning(self):
        """Empty policy_verdicts should produce no BLOCKER warnings."""
        resolution = {
            "recommended_action": "APPROVE",
            "risk_level": "LOW",
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("BLOCKER" in w for w in warnings)

    def test_confidence_exactly_at_threshold(self):
        """Confidence exactly 0.95 should NOT trigger warning (> not >=)."""
        resolution = {
            "recommended_action": "REJECT",
            "confidence": 0.95,
            "policy_verdicts": [
                {"policy_code": "POL-001", "verdict": "FAIL", "reasoning": "x"},
                {"policy_code": "POL-002", "verdict": "FAIL", "reasoning": "y"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Confianza excesiva" in w for w in warnings)

    def test_confidence_just_over_threshold(self):
        """Confidence 0.951 with 2 FAILs should trigger warning."""
        resolution = {
            "recommended_action": "REJECT",
            "confidence": 0.951,
            "policy_verdicts": [
                {"policy_code": "POL-001", "verdict": "FAIL", "reasoning": "x"},
                {"policy_code": "POL-002", "verdict": "FAIL", "reasoning": "y"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert any("Confianza excesiva" in w for w in warnings)

    def test_high_confidence_with_only_one_fail(self):
        """Confidence > 0.95 with only 1 FAIL should NOT trigger warning."""
        resolution = {
            "recommended_action": "REJECT",
            "confidence": 0.99,
            "policy_verdicts": [
                {"policy_code": "POL-001", "verdict": "FAIL", "reasoning": "x"},
                {"policy_code": "POL-002", "verdict": "PASS", "reasoning": "y"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Confianza excesiva" in w for w in warnings)

    def test_combined_blocker_and_compensation(self):
        """APPROVE+BLOCKER + excessive compensation should produce 2 warnings."""
        resolution = {
            "recommended_action": "APPROVE",
            "risk_level": "MEDIUM",
            "requires_hitl": True,
            "compensation_amount_usd": 200.0,
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert len(warnings) == 2
        assert any("BLOCKER" in w for w in warnings)
        assert any("Compensacion" in w for w in warnings)

    def test_no_compensation_field_no_warning(self):
        """Missing compensation_amount_usd field should not crash."""
        resolution = {
            "recommended_action": "REJECT",
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Compensacion" in w for w in warnings)

    def test_no_confidence_field_no_warning(self):
        """Missing confidence field should not crash."""
        resolution = {
            "recommended_action": "REJECT",
            "policy_verdicts": [
                {"policy_code": "POL-001", "verdict": "FAIL", "reasoning": "x"},
                {"policy_code": "POL-002", "verdict": "FAIL", "reasoning": "y"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Confianza excesiva" in w for w in warnings)

    def test_escalate_action_with_blocker_no_correction(self):
        """ESCALATE action with BLOCKER should NOT be auto-corrected (only APPROVE is)."""
        resolution = {
            "recommended_action": "ESCALATE",
            "risk_level": "HIGH",
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("BLOCKER" in w for w in warnings)
        assert resolution["recommended_action"] == "ESCALATE"
