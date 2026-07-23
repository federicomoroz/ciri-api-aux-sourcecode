"""Unit tests for ResolutionService._validate_resolution guardrails."""

import pytest
from api.app.services.resolution import ResolutionService


class TestGuardrailApproveWithBlocker:
    """Guardrail 1: APPROVE + BLOCKER active → auto-correct to REJECT."""

    def test_approve_with_blocker_corrected_to_reject(self):
        resolution = {
            "recommended_action": "APPROVE",
            "risk_level": "MEDIUM",
            "requires_hitl": True,
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)

        assert len(warnings) == 1
        assert "GUARDRAIL" in warnings[0]
        assert "BLOCKER" in warnings[0]
        assert resolution["recommended_action"] == "REJECT"
        assert resolution["risk_level"] == "BLOCKER"
        assert resolution["requires_hitl"] is False

    def test_reject_with_blocker_no_correction(self):
        resolution = {
            "recommended_action": "REJECT",
            "risk_level": "BLOCKER",
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert len(warnings) == 0

    def test_approve_without_blocker_no_correction(self):
        resolution = {
            "recommended_action": "APPROVE",
            "risk_level": "LOW",
            "policy_verdicts": [
                {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert len(warnings) == 0


class TestGuardrailBlockerWithoutBlockerVerdicts:
    """Guardrail 4: risk_level=BLOCKER without actual BLOCKER verdicts → auto-correct to HIGH."""

    def test_blocker_risk_without_blocker_verdicts_corrected_to_high_hitl(self):
        resolution = {
            "recommended_action": "REJECT",
            "risk_level": "BLOCKER",
            "requires_hitl": False,
            "policy_verdicts": [
                {"policy_code": "POL-CB-005", "verdict": "FAIL", "reasoning": "Requiere aprobacion"},
                {"policy_code": "POL-CB-004", "verdict": "FAIL", "reasoning": "CB ratio alto"},
            ],
        }
        tx = {"amount_usd": 500.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)

        assert any("risk_level=BLOCKER sin veredictos BLOCKER" in w for w in warnings)
        assert resolution["risk_level"] == "HIGH"
        assert resolution["recommended_action"] == "PENDING_HITL"
        assert resolution["requires_hitl"] is True

    def test_blocker_risk_with_blocker_verdicts_no_correction(self):
        resolution = {
            "recommended_action": "REJECT",
            "risk_level": "BLOCKER",
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            ],
        }
        tx = {"amount_usd": 500.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("risk_level=BLOCKER sin veredictos" in w for w in warnings)

    def test_high_risk_without_blocker_verdicts_no_correction(self):
        resolution = {
            "recommended_action": "PENDING_HITL",
            "risk_level": "HIGH",
            "policy_verdicts": [
                {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
            ],
        }
        tx = {"amount_usd": 500.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("risk_level=BLOCKER" in w for w in warnings)


class TestGuardrailRejectWithoutBlocker:
    """Guardrail 5: REJECT without BLOCKER verdicts → auto-correct to PENDING_HITL."""

    def test_reject_without_blocker_corrected_to_hitl(self):
        resolution = {
            "recommended_action": "REJECT",
            "risk_level": "HIGH",
            "requires_hitl": False,
            "policy_verdicts": [
                {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
                {"policy_code": "POL-CB-004", "verdict": "FAIL", "reasoning": "CB ratio"},
            ],
        }
        tx = {"amount_usd": 500.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)

        assert any("REJECT sin veredictos BLOCKER" in w for w in warnings)
        assert resolution["recommended_action"] == "PENDING_HITL"
        assert resolution["requires_hitl"] is True

    def test_reject_with_blocker_no_correction(self):
        resolution = {
            "recommended_action": "REJECT",
            "risk_level": "BLOCKER",
            "policy_verdicts": [
                {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            ],
        }
        tx = {"amount_usd": 500.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("REJECT sin veredictos BLOCKER" in w for w in warnings)
        assert resolution["recommended_action"] == "REJECT"


class TestGuardrailCompensation:
    """Guardrail 2: compensation > 110% of transaction amount."""

    def test_excessive_compensation_warning(self):
        resolution = {
            "recommended_action": "APPROVE",
            "compensation_amount_usd": 150.0,
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert any("Compensacion" in w for w in warnings)

    def test_normal_compensation_no_warning(self):
        resolution = {
            "recommended_action": "APPROVE",
            "compensation_amount_usd": 15.0,
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Compensacion" in w for w in warnings)


class TestGuardrailExcessiveConfidence:
    """Guardrail 3: confidence > 0.95 with 2+ FAIL/BLOCKER verdicts."""

    def test_high_confidence_with_multiple_fails(self):
        resolution = {
            "recommended_action": "REJECT",
            "confidence": 0.98,
            "policy_verdicts": [
                {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "score bajo"},
                {"policy_code": "POL-FRD-002", "verdict": "FAIL", "reasoning": "geo anomaly"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert any("Confianza excesiva" in w for w in warnings)

    def test_normal_confidence_no_warning(self):
        resolution = {
            "recommended_action": "REJECT",
            "confidence": 0.85,
            "policy_verdicts": [
                {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "score bajo"},
                {"policy_code": "POL-FRD-002", "verdict": "FAIL", "reasoning": "geo anomaly"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Confianza excesiva" in w for w in warnings)
