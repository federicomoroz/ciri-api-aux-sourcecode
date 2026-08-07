"""Unit tests for ResolutionService guardrails and deterministic outcome."""

from api.app.domain.context import CaseContext
from api.app.domain.enums import PaymentMethod, ResolutionOutcome, RiskLevel, VerdictType
from api.app.services.resolution import ResolutionService


class TestDetectDivergence:
    """El modelo contradice a sus propios veredictos: hay que dejar constancia.

    Se comprueba sobre la propuesta del modelo, antes del override determinista.
    Despues del override la contradiccion ya no es observable.
    """

    BLOCKER_VERDICTS = [
        {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": PaymentMethod.CRYPTO},
    ]
    FAIL_VERDICTS = [
        {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
        {"policy_code": "POL-CB-004", "verdict": VerdictType.FAIL, "reasoning": "CB ratio"},
    ]

    def test_approve_con_blocker_queda_registrado(self):
        propuesta = {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.MEDIUM}
        outcome = {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER}
        warnings = ResolutionService._detect_divergence(
            propuesta, outcome, self.BLOCKER_VERDICTS,
        )
        assert len(warnings) == 1
        assert ResolutionOutcome.APPROVE in warnings[0]
        assert VerdictType.BLOCKER in warnings[0]
        assert "alucinacion" in warnings[0]

    def test_reject_con_blocker_es_coherente(self):
        propuesta = {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER}
        outcome = {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER}
        warnings = ResolutionService._detect_divergence(
            propuesta, outcome, self.BLOCKER_VERDICTS,
        )
        assert warnings == []

    def test_approve_sin_blocker_es_coherente(self):
        propuesta = {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW}
        outcome = {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW}
        verdicts = [{"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "ok"}]
        assert ResolutionService._detect_divergence(propuesta, outcome, verdicts) == []

    def test_riesgo_blocker_inventado_queda_registrado(self):
        propuesta = {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER}
        outcome = {"recommended_action": ResolutionOutcome.PENDING_HITL, "risk_level": RiskLevel.HIGH}
        warnings = ResolutionService._detect_divergence(
            propuesta, outcome, self.FAIL_VERDICTS,
        )
        assert any("risk_level=BLOCKER sin veredictos BLOCKER" in w for w in warnings)
        assert any("corregido a HIGH" in w for w in warnings)

    def test_reject_sin_blocker_queda_registrado(self):
        propuesta = {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.HIGH}
        outcome = {"recommended_action": ResolutionOutcome.PENDING_HITL, "risk_level": RiskLevel.HIGH}
        warnings = ResolutionService._detect_divergence(
            propuesta, outcome, self.FAIL_VERDICTS,
        )
        assert any("REJECT sin veredictos BLOCKER" in w for w in warnings)
        assert any("corregido a PENDING_HITL" in w for w in warnings)

    def test_sin_veredictos_no_inventa_advertencias(self):
        propuesta = {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW}
        outcome = {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW}
        assert ResolutionService._detect_divergence(propuesta, outcome, []) == []

    def test_no_muta_la_propuesta(self):
        """Detectar no es corregir: de eso se encarga el override."""
        propuesta = {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.MEDIUM}
        outcome = {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER}
        ResolutionService._detect_divergence(propuesta, outcome, self.BLOCKER_VERDICTS)
        assert propuesta == {"recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.MEDIUM}


class TestGuardrailCompensation:
    """Guardrail 2: compensation > 110% of transaction amount."""

    def test_excessive_compensation_warning(self):
        resolution = {
            "recommended_action": ResolutionOutcome.APPROVE,
            "compensation_amount_usd": 150.0,
            "policy_verdicts": [],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert any("Compensacion" in w for w in warnings)

    def test_normal_compensation_no_warning(self):
        resolution = {
            "recommended_action": ResolutionOutcome.APPROVE,
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
            "recommended_action": ResolutionOutcome.REJECT,
            "confidence": 0.98,
            "policy_verdicts": [
                {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "score bajo"},
                {"policy_code": "POL-FRD-002", "verdict": VerdictType.FAIL, "reasoning": "geo anomaly"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert any("Confianza excesiva" in w for w in warnings)

    def test_normal_confidence_no_warning(self):
        resolution = {
            "recommended_action": ResolutionOutcome.REJECT,
            "confidence": 0.85,
            "policy_verdicts": [
                {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "score bajo"},
                {"policy_code": "POL-FRD-002", "verdict": VerdictType.FAIL, "reasoning": "geo anomaly"},
            ],
        }
        tx = {"amount_usd": 100.0}
        warnings = ResolutionService._validate_resolution(resolution, tx)
        assert not any("Confianza excesiva" in w for w in warnings)


class TestDetermineOutcome:
    """Deterministic outcome: code decides action/risk from policy verdicts."""

    def test_blocker_verdict_returns_reject(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": PaymentMethod.CRYPTO},
            {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
        ]
        tx = {"fraud_score": 8}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.REJECT
        assert outcome["risk_level"] == RiskLevel.BLOCKER
        assert outcome["requires_hitl"] is False
        assert outcome["hitl_reason"] is None

    def test_multiple_fails_returns_pending_hitl_high(self):
        verdicts = [
            {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
            {"policy_code": "POL-CB-004", "verdict": VerdictType.FAIL, "reasoning": "CB ratio alto"},
        ]
        tx = {"fraud_score": 25}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.PENDING_HITL
        assert outcome["risk_level"] == RiskLevel.HIGH
        assert outcome["requires_hitl"] is True
        assert "2 violacion" in outcome["hitl_reason"]

    def test_single_fail_returns_pending_hitl_medium(self):
        verdicts = [
            {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "SLA ok"},
        ]
        tx = {"fraud_score": 25}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.PENDING_HITL
        assert outcome["risk_level"] == RiskLevel.MEDIUM
        assert outcome["requires_hitl"] is True

    def test_single_fail_with_low_fraud_score_returns_high(self):
        verdicts = [
            {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
        ]
        tx = {"fraud_score": 8}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.PENDING_HITL
        assert outcome["risk_level"] == RiskLevel.HIGH

    def test_all_pass_returns_approve_low(self):
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "SLA ok"},
            {"policy_code": "POL-CB-001", "verdict": VerdictType.PASS, "reasoning": "Doc ok"},
        ]
        tx = {"fraud_score": 85}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.APPROVE
        assert outcome["risk_level"] == RiskLevel.LOW
        assert outcome["requires_hitl"] is False
        assert outcome["hitl_reason"] is None

    def test_all_pass_medium_fraud_score_returns_approve_medium(self):
        """fraud_score between 15-30 with no FAILs → APPROVE but risk MEDIUM."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "SLA ok"},
        ]
        tx = {"fraud_score": 20}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.APPROVE
        assert outcome["risk_level"] == RiskLevel.MEDIUM

    def test_no_fraud_score_uses_default(self):
        """Missing fraud_score defaults to 50 (safe)."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "SLA ok"},
        ]
        tx = {}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.APPROVE
        assert outcome["risk_level"] == RiskLevel.LOW

    def test_warning_verdicts_treated_as_pass(self):
        """WARNING verdicts don't count as failures."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.WARNING, "reasoning": "SLA close"},
            {"policy_code": "POL-CB-001", "verdict": VerdictType.PASS, "reasoning": "Doc ok"},
        ]
        tx = {"fraud_score": 50}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.APPROVE
        assert outcome["risk_level"] == RiskLevel.LOW

    def test_requires_human_review_forces_pending_hitl(self):
        """Even without FAILs, requires_human_review=true → PENDING_HITL."""
        verdicts = [
            {"policy_code": "POL-CB-005", "verdict": VerdictType.WARNING, "reasoning": "Needs review",
             "requires_human_review": True},
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "SLA ok"},
        ]
        tx = {"fraud_score": 85}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.PENDING_HITL
        assert outcome["requires_hitl"] is True
        assert "revision humana" in outcome["hitl_reason"]

    def test_requires_human_review_false_no_effect(self):
        """requires_human_review=false doesn't force PENDING_HITL."""
        verdicts = [
            {"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS, "reasoning": "SLA ok",
             "requires_human_review": False},
        ]
        tx = {"fraud_score": 85}
        outcome = ResolutionService._determine_outcome(verdicts, tx)

        assert outcome["recommended_action"] == ResolutionOutcome.APPROVE


class TestSanitizeVerdicts:
    """Downgrade invalid BLOCKER verdicts to FAIL."""

    def test_non_whitelisted_blocker_downgraded_to_fail(self):
        verdicts = [
            {"policy_code": "POL-CB-004", "verdict": VerdictType.BLOCKER, "reasoning": "Suspended"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == VerdictType.FAIL
        assert result[0]["requires_human_review"] is True

    def test_whitelisted_blocker_preserved(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": PaymentMethod.CRYPTO},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == VerdictType.BLOCKER

    def test_fail_verdicts_unchanged(self):
        verdicts = [
            {"policy_code": "POL-CB-004", "verdict": VerdictType.FAIL, "reasoning": "CB ratio alto"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == VerdictType.FAIL

    def test_mixed_verdicts_only_invalid_blockers_downgraded(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": PaymentMethod.CRYPTO},
            {"policy_code": "POL-CB-004", "verdict": VerdictType.BLOCKER, "reasoning": "Suspended"},
            {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts)

        assert result[0]["verdict"] == VerdictType.BLOCKER  # POL-EXC-003 preserved
        assert result[1]["verdict"] == VerdictType.FAIL      # POL-CB-004 downgraded
        assert result[2]["verdict"] == VerdictType.FAIL      # unchanged


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


class TestGuardrailsEnElPipelineCompleto:
    """El guardrail tiene que dispararse en resolve(), no solo en aislamiento.

    Regresion: las comprobaciones vivian despues del override determinista, que
    ya habia reescrito la accion y el riesgo. Eran inalcanzables — la alucinacion
    se corregia en silencio y no quedaba registrada en ningun lado.
    """

    TX = {
        "id": "TXN-00051", "merchant": "Airbnb", "amount_usd": 100.0,
        "fraud_score": 8, "country": "COL", "payment_method": PaymentMethod.CRYPTO, "channel": "POS",
    }
    POLICIES = [{
        "code": "POL-EXC-003", "name": "Exclusion cripto", "category": "EXCEPCION",
        "description": "Las transacciones con criptomonedas son irreversibles.",
        "reference": "Reg. Fintech 2024/03",
    }]

    @staticmethod
    def _servicio(sintesis: dict, veredicto: str = VerdictType.BLOCKER):
        import json

        from api.app.llm.client import LLMResult
        from api.app.observability.tracer import NoOpTracer

        class LLMGuionado:
            def __init__(self):
                self.llamadas = 0

            def complete(self, system, user, trace_id="", **kwargs):
                self.llamadas += 1
                payload = (
                    [{"policy_code": "POL-EXC-003", "verdict": veredicto,
                      "reasoning": "cripto irreversible", "requires_human_review": False}]
                    if self.llamadas == 1 else sintesis
                )
                return LLMResult(text=json.dumps(payload), input_tokens=1, output_tokens=1)

        return ResolutionService(llm=LLMGuionado(), tracer=NoOpTracer())

    def _resolver(
        self, sintesis: dict, veredicto: str = VerdictType.BLOCKER, sla: dict | None = None,
    ) -> dict:
        return self._servicio(sintesis, veredicto).resolve(
            CaseContext(
                transaction=self.TX, policies=self.POLICIES,
                motivo="No reconoce la compra", cliente_vip=False,
                sla=sla or {},
            )
        )

    def test_approve_alucinado_sobre_blocker_queda_registrado(self):
        r = self._resolver({
            "recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW,
            "confidence": 0.99, "justification": "todo bien",
        })
        assert r["recommended_action"] == ResolutionOutcome.REJECT
        assert r["risk_level"] == RiskLevel.BLOCKER
        assert any(ResolutionOutcome.APPROVE in w and "alucinacion" in w for w in r["guardrail_warnings"]), (
            "la alucinacion se corrigio pero no quedo registrada"
        )

    def test_reject_inventado_sin_blocker_queda_registrado(self):
        r = self._resolver(
            {"recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER,
             "confidence": 0.9, "justification": "x"},
            veredicto=VerdictType.FAIL,
        )
        assert r["recommended_action"] == ResolutionOutcome.PENDING_HITL
        assert any("REJECT sin veredictos BLOCKER" in w for w in r["guardrail_warnings"])
        assert any("risk_level=BLOCKER" in w for w in r["guardrail_warnings"])

    def test_propuesta_coherente_no_genera_ruido(self):
        r = self._resolver({
            "recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER,
            "confidence": 0.8, "justification": "cripto irreversible",
        })
        assert r["recommended_action"] == ResolutionOutcome.REJECT
        assert r["guardrail_warnings"] == []

    # ── Compensacion: la decide el SLA, no el modelo ────────────────────

    DENTRO_DE_SLA = {"within_sla": True, "days_elapsed": 3, "sla_limit_days": 10,
                     "sla_type": "standard", "compensation_applicable": False}
    FUERA_DE_SLA = {"within_sla": False, "days_elapsed": 14, "sla_limit_days": 10,
                    "sla_type": "standard", "compensation_applicable": True}

    COHERENTE = {
        "recommended_action": ResolutionOutcome.REJECT, "risk_level": RiskLevel.BLOCKER,
        "confidence": 0.8, "justification": "cripto irreversible",
    }

    def test_el_sla_incumplido_habilita_compensacion_aunque_el_modelo_diga_que_no(self):
        r = self._resolver(
            {**self.COHERENTE, "compensation_applicable": False, "compensation_amount_usd": 0.0},
            sla=self.FUERA_DE_SLA,
        )
        assert r["compensation_applicable"] is True
        assert r["compensation_amount_usd"] == 15.0   # tope POL-SLA-004

    def test_el_sla_cumplido_anula_la_compensacion_que_invento_el_modelo(self):
        r = self._resolver(
            {**self.COHERENTE, "compensation_applicable": True, "compensation_amount_usd": 15.0},
            sla=self.DENTRO_DE_SLA,
        )
        assert r["compensation_applicable"] is False
        assert r["compensation_amount_usd"] == 0.0
        assert any("compensation_applicable=True" in w and "POL-SLA-004" in w
                   for w in r["guardrail_warnings"]), (
            "la contradiccion con el SLA se corrigio pero no quedo registrada"
        )

    def test_coincidir_con_el_sla_no_genera_advertencia(self):
        r = self._resolver(
            {**self.COHERENTE, "compensation_applicable": True, "compensation_amount_usd": 15.0},
            sla=self.FUERA_DE_SLA,
        )
        assert r["compensation_applicable"] is True
        assert r["guardrail_warnings"] == []

    def test_sin_dato_de_sla_no_se_toca_lo_que_propuso_el_modelo(self):
        """Sin SLA no hay nada que determinar: forzar false seria inventar igual."""
        r = self._resolver(
            {**self.COHERENTE, "compensation_applicable": True, "compensation_amount_usd": 15.0},
            sla={},
        )
        assert r["compensation_applicable"] is True
        assert r["guardrail_warnings"] == []


class TestDetermineCompensation:
    """POL-SLA-004 calculado por codigo: dias habiles vs limite, con tope."""

    TX = {"amount_usd": 100.0}

    def test_fuera_de_sla_habilita_el_tope(self):
        r = ResolutionService._determine_compensation(
            {"within_sla": False}, self.TX,
        )
        assert r == {"compensation_applicable": True, "compensation_amount_usd": 15.0}

    def test_dentro_de_sla_no_compensa(self):
        r = ResolutionService._determine_compensation(
            {"within_sla": True}, self.TX,
        )
        assert r == {"compensation_applicable": False, "compensation_amount_usd": 0.0}

    def test_no_compensa_mas_que_el_cargo_original(self):
        """Un cargo de USD 4 no puede generar una compensacion de USD 15."""
        r = ResolutionService._determine_compensation(
            {"within_sla": False}, {"amount_usd": 4.0},
        )
        assert r["compensation_amount_usd"] == 4.0

    def test_sin_sla_no_determina_nada(self):
        assert ResolutionService._determine_compensation({}, self.TX) == {}
        assert ResolutionService._determine_compensation({"days_elapsed": 3}, self.TX) == {}
