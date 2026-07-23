"""Unit tests for ResolutionService guardrails and deterministic outcome."""

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


class TestDetermineOutcome:
    """Deterministic outcome: code decides action/risk from policy verdicts."""

    def test_blocker_verdict_returns_reject(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
        ]
        tx = {"fraud_score": 8}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "REJECT"
        assert outcome["risk_level"] == "BLOCKER"
        assert outcome["requires_hitl"] is False
        assert outcome["hitl_reason"] is None

    def test_multiple_fails_returns_pending_hitl_high(self):
        verdicts = [
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
            {"policy_code": "POL-CB-004", "verdict": "FAIL", "reasoning": "CB ratio alto"},
        ]
        tx = {"fraud_score": 25}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "PENDING_HITL"
        assert outcome["risk_level"] == "HIGH"
        assert outcome["requires_hitl"] is True
        assert "2 violacion" in outcome["hitl_reason"]

    def test_single_fail_returns_pending_hitl_medium(self):
        verdicts = [
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
            {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok"},
        ]
        tx = {"fraud_score": 25}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "PENDING_HITL"
        assert outcome["risk_level"] == "MEDIUM"
        assert outcome["requires_hitl"] is True

    def test_single_fail_with_low_fraud_score_returns_high(self):
        verdicts = [
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
        ]
        tx = {"fraud_score": 8}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "PENDING_HITL"
        assert outcome["risk_level"] == "HIGH"

    def test_all_pass_returns_approve_low(self):
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok"},
            {"policy_code": "POL-CB-001", "verdict": "PASS", "reasoning": "Doc ok"},
        ]
        tx = {"fraud_score": 85}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "APPROVE"
        assert outcome["risk_level"] == "LOW"
        assert outcome["requires_hitl"] is False
        assert outcome["hitl_reason"] is None

    def test_all_pass_medium_fraud_score_returns_approve_medium(self):
        """fraud_score between 15-30 with no FAILs → APPROVE but risk MEDIUM."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok"},
        ]
        tx = {"fraud_score": 20}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "APPROVE"
        assert outcome["risk_level"] == "MEDIUM"

    def test_no_fraud_score_uses_default(self):
        """Missing fraud_score defaults to 50 (safe)."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok"},
        ]
        tx = {}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "APPROVE"
        assert outcome["risk_level"] == "LOW"

    def test_warning_verdicts_treated_as_pass(self):
        """WARNING verdicts don't count as failures."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": "WARNING", "reasoning": "SLA close"},
            {"policy_code": "POL-CB-001", "verdict": "PASS", "reasoning": "Doc ok"},
        ]
        tx = {"fraud_score": 50}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "APPROVE"
        assert outcome["risk_level"] == "LOW"

    def test_requires_human_review_forces_pending_hitl(self):
        """Even without FAILs, requires_human_review=true → PENDING_HITL."""
        verdicts = [
            {"policy_code": "POL-CB-005", "verdict": "WARNING", "reasoning": "Needs review",
             "requires_human_review": True},
            {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok"},
        ]
        tx = {"fraud_score": 85}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "PENDING_HITL"
        assert outcome["requires_hitl"] is True
        assert "revision humana" in outcome["hitl_reason"]

    def test_requires_human_review_false_no_effect(self):
        """requires_human_review=false doesn't force PENDING_HITL."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": "PASS", "reasoning": "SLA ok",
             "requires_human_review": False},
        ]
        tx = {"fraud_score": 85}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == "APPROVE"


class TestSanitizeVerdicts:
    """Downgrade invalid BLOCKER verdicts to FAIL."""

    def test_non_whitelisted_blocker_downgraded_to_fail(self):
        verdicts = [
            {"policy_code": "POL-CB-004", "verdict": "BLOCKER", "reasoning": "Suspended"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == "FAIL"
        assert result[0]["requires_human_review"] is True

    def test_whitelisted_blocker_preserved(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == "BLOCKER"

    def test_fail_verdicts_unchanged(self):
        verdicts = [
            {"policy_code": "POL-CB-004", "verdict": "FAIL", "reasoning": "CB ratio alto"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == "FAIL"

    def test_mixed_verdicts_only_invalid_blockers_downgraded(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "Cripto"},
            {"policy_code": "POL-CB-004", "verdict": "BLOCKER", "reasoning": "Suspended"},
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "Score bajo"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == "BLOCKER"  # POL-EXC-003 preserved
        assert result[1]["verdict"] == "FAIL"      # POL-CB-004 downgraded
        assert result[2]["verdict"] == "FAIL"      # unchanged


class TestBuildPrecedentSummary:
    """Deterministic precedent summary generation."""

    def test_empty_cases_returns_placeholder(self):
        result = ResolutionService._build_precedent_summary([], "Cargo duplicado")
        assert result == "Sin precedentes relevantes."

    def test_matching_motivo_tagged_and_first(self):
        cases = [
            {"case_id": "CB-001", "motivo": "Defecto", "resolution": "Reembolso",
             "resolution_days": 5, "merchant": "Amazon"},
            {"case_id": "CB-002", "motivo": "Cargo doble", "resolution": "Aprobado",
             "resolution_days": 3, "merchant": "Rappi",
             "observations": "Timeout en gateway"},
        ]
        result = ResolutionService._build_precedent_summary(cases, "Cargo duplicado")
        assert "[MOTIVO SIMILAR]" in result
        # CB-002 should come first (match)
        assert result.index("CB-002") < result.index("CB-001")
        # Observations included for match
        assert "Timeout en gateway" in result
        # Relevance label included
        assert "Relevancia: mismo patron de cargo duplicado" in result

    def test_observations_matched(self):
        """Match via observations, not just motivo field."""
        cases = [
            {"case_id": "CB-038", "motivo": "Monto incorrecto", "resolution": "Cerrado",
             "resolution_days": 24, "merchant": "Rappi",
             "observations": "Error en sistema de pagos — cargo doble por timeout"},
        ]
        result = ResolutionService._build_precedent_summary(cases, "Cargo duplicado")
        assert "[MOTIVO SIMILAR]" in result
        assert "cargo doble por timeout" in result
        assert "Relevancia: mismo patron de cargo duplicado" in result

    def test_no_motivo_no_tags(self):
        cases = [
            {"case_id": "CB-001", "motivo": "Fraude", "resolution": "Rechazado",
             "resolution_days": 2, "merchant": "eBay"},
        ]
        result = ResolutionService._build_precedent_summary(cases, None)
        assert "[MOTIVO SIMILAR]" not in result
        assert "CB-001" in result

    def test_includes_merchant(self):
        cases = [
            {"case_id": "CB-001", "motivo": "Fraude", "resolution": "Aprobado",
             "resolution_days": 3, "merchant": "MercadoLibre"},
        ]
        result = ResolutionService._build_precedent_summary(cases, "Fraude")
        assert "merchant=MercadoLibre" in result
