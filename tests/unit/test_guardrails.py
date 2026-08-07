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
    """Solo puede emitir BLOCKER la politica marcada como bloqueante.

    El permiso sale de la politica (`puede_bloquear`, columna de SQLite que
    viaja en la carga de Qdrant), no de una lista en el codigo: habilitar una
    politica bloqueante nueva es un POST, no un deploy.
    """

    BLOQUEANTE = {"code": "POL-EXC-003", "puede_bloquear": True}
    COMUN = {"code": "POL-CB-004", "puede_bloquear": False}
    POLITICAS = [BLOQUEANTE, COMUN, {"code": "POL-FRD-001", "puede_bloquear": False}]

    def test_non_whitelisted_blocker_downgraded_to_fail(self):
        verdicts = [
            {"policy_code": "POL-CB-004", "verdict": VerdictType.BLOCKER, "reasoning": "Suspended"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts, self.POLITICAS)

        assert result[0]["verdict"] == VerdictType.FAIL
        assert result[0]["requires_human_review"] is True

    def test_whitelisted_blocker_preserved(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": PaymentMethod.CRYPTO},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts, self.POLITICAS)

        assert result[0]["verdict"] == VerdictType.BLOCKER

    def test_fail_verdicts_unchanged(self):
        verdicts = [
            {"policy_code": "POL-CB-004", "verdict": VerdictType.FAIL, "reasoning": "CB ratio alto"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts, self.POLITICAS)

        assert result[0]["verdict"] == VerdictType.FAIL

    def test_mixed_verdicts_only_invalid_blockers_downgraded(self):
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": PaymentMethod.CRYPTO},
            {"policy_code": "POL-CB-004", "verdict": VerdictType.BLOCKER, "reasoning": "Suspended"},
            {"policy_code": "POL-FRD-001", "verdict": VerdictType.FAIL, "reasoning": "Score bajo"},
        ]
        result = ResolutionService._sanitize_verdicts(verdicts, self.POLITICAS)

        assert result[0]["verdict"] == VerdictType.BLOCKER  # POL-EXC-003 preserved
        assert result[1]["verdict"] == VerdictType.FAIL      # POL-CB-004 downgraded
        assert result[2]["verdict"] == VerdictType.FAIL      # unchanged

    def test_una_politica_nueva_puede_bloquear_sin_deploy(self):
        """Regresion: la whitelist estaba en constants.py.

        Cargar una politica que dijera «rechazar automaticamente» la indexaba y
        la llevaba al contexto, pero nunca podia producir un rechazo.
        """
        verdicts = [{"policy_code": "POL-AUD-001", "verdict": VerdictType.BLOCKER, "reasoning": "x"}]
        nuevas = [{"code": "POL-AUD-001", "puede_bloquear": True}]
        assert ResolutionService._sanitize_verdicts(verdicts, nuevas)[0]["verdict"] == VerdictType.BLOCKER

    def test_un_indice_viejo_sin_el_campo_cae_a_la_semilla(self):
        """Una coleccion armada antes de que la columna existiera sigue funcionando."""
        sin_campo = [{"code": "POL-EXC-003"}, {"code": "POL-CB-004"}]
        verdicts = [
            {"policy_code": "POL-EXC-003", "verdict": VerdictType.BLOCKER, "reasoning": "cripto"},
            {"policy_code": "POL-CB-004", "verdict": VerdictType.BLOCKER, "reasoning": "suspendido"},
        ]
        r = ResolutionService._sanitize_verdicts(verdicts, sin_campo)
        assert r[0]["verdict"] == VerdictType.BLOCKER
        assert r[1]["verdict"] == VerdictType.FAIL


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


class TestSinVeredictosFallaCerrado:
    """«Ninguna politica fallo» y «no se evaluo ninguna politica» no son lo mismo.

    Regresion: `_determine_outcome([])` devolvia APPROVE con `requires_hitl=False`.
    Se llega ahi por dos caminos reales —Qdrant caido, que el nodo `Buscar
    Politicas` deja pasar con `continueRegularOutput`, o un JSON invalido del
    modelo, que `validate_llm_output` degrada a lista vacia—, o sea que una falla
    de infraestructura aprobaba contracargos sola.
    """

    def test_sin_veredictos_no_aprueba(self):
        r = ResolutionService._determine_outcome([], {"fraud_score": 8})
        assert r["recommended_action"] != ResolutionOutcome.APPROVE
        assert r["recommended_action"] == ResolutionOutcome.PENDING_HITL

    def test_sin_veredictos_pide_una_persona(self):
        r = ResolutionService._determine_outcome([], {"fraud_score": 90})
        assert r["requires_hitl"] is True
        assert "politica" in r["hitl_reason"].lower()

    def test_el_motivo_del_riesgo_dice_que_no_hubo_evidencia(self):
        r = ResolutionService._determine_outcome([], {})
        assert r["risk_level"] == RiskLevel.HIGH
        assert "evidencia" in r["risk_reason"].lower()

    def test_con_un_solo_pass_si_puede_aprobar(self):
        """La rama nueva no debe tragarse el caso normal."""
        r = ResolutionService._determine_outcome(
            [{"policy_code": "POL-SLA-002", "verdict": VerdictType.PASS}], {"fraud_score": 90},
        )
        assert r["recommended_action"] == ResolutionOutcome.APPROVE


class TestElJuezCalificaAlModelo:
    """El Juez evaluaba la resolucion ya corregida por el override.

    `policy_consistency` y `risk_assessment` califican la accion y el nivel de
    riesgo — dos campos que el codigo fija siempre. Sobre la version entregada no
    podian bajar de 10 por construccion: dos de los cinco criterios eran ruido, y
    el score global salia inflado. Ahora se les pasa la propuesta original.
    """

    PROPUESTA_ALUCINADA = {
        "recommended_action": ResolutionOutcome.APPROVE, "risk_level": RiskLevel.LOW,
        "confidence": 0.99, "justification": "todo bien",
    }

    def test_la_propuesta_se_captura_antes_del_override(self):
        r = TestGuardrailsEnElPipelineCompleto()._resolver(self.PROPUESTA_ALUCINADA)
        propuesta = r["_propuesta_del_modelo"]
        assert propuesta["recommended_action"] == ResolutionOutcome.APPROVE, (
            "se capturo despues del override: quedo la decision del codigo"
        )
        assert r["recommended_action"] == ResolutionOutcome.REJECT, "el override tiene que aplicarse igual"

    def test_lleva_los_campos_que_el_override_pisa(self):
        r = TestGuardrailsEnElPipelineCompleto()._resolver(self.PROPUESTA_ALUCINADA)
        assert set(r["_propuesta_del_modelo"]) == {
            "recommended_action", "risk_level", "requires_hitl",
            "compensation_applicable", "confidence",
        }

    def test_el_prompt_del_juez_recibe_la_propuesta(self):
        from api.app.llm import prompts

        _, user = prompts.v1_judge.render(
            full_context={"transaction": {"id": "TXN-00051"}},
            resolution={"recommended_action": ResolutionOutcome.REJECT},
            propuesta={"recommended_action": ResolutionOutcome.APPROVE},
        )
        assert "PROPUESTA ORIGINAL DEL MODELO" in user
        assert "APPROVE" in user.split("PROPUESTA ORIGINAL DEL MODELO")[1]

    def test_sin_propuesta_el_prompt_no_inventa_la_seccion(self):
        from api.app.llm import prompts

        _, user = prompts.v1_judge.render(
            full_context={}, resolution={"recommended_action": ResolutionOutcome.REJECT},
        )
        assert "PROPUESTA ORIGINAL DEL MODELO" not in user

    def test_la_propuesta_no_viaja_dentro_de_la_resolucion_que_se_juzga(self):
        """El Juez no debe ver el campo interno duplicado en la resolucion."""
        from api.app.observability.tracer import NoOpTracer

        capturado = {}

        class LLMEspia:
            def complete(self, system, user, trace_id="", **kwargs):
                capturado["user"] = user
                from api.app.llm.client import LLMResult
                return LLMResult(text='{"overall_score": 8.0, "criteria": {}}', input_tokens=1, output_tokens=1)

        svc = ResolutionService(llm=LLMEspia(), tracer=NoOpTracer())
        svc.judge(
            resolution={"recommended_action": ResolutionOutcome.REJECT,
                        "_propuesta_del_modelo": {"recommended_action": ResolutionOutcome.APPROVE}},
            full_context={"transaction": {"id": "TXN-00051"}},
        )
        entregada = capturado["user"].split("## RESOLUCION ENTREGADA")[1].split("##")[0]
        assert "_propuesta_del_modelo" not in entregada


class TestUnVeredictoIlegibleNoEsUnVeredictoFavorable:
    """Un enum mal escrito auto-aprobaba el contracargo.

    `validate_llm_output` no traduce los veredictos invalidos a proposito
    —adivinar el mas parecido seria decidir por el modelo, y
    `test_un_veredicto_que_no_existe_no_se_traduce` lo fija—, asi que llegaban
    crudos a `_determine_outcome`. Ahi `has_blocker` daba False y `fail_count`
    cero: un veredicto que decia BLOCKED valia lo mismo que uno que dice PASS.

    Reproducido sobre el paquete entregado: un caso cripto con score 8 cuyos dos
    veredictos venian como `BLOCKED` y `FAILED` salia **APPROVE, sin HITL y sin
    un solo warning**. Con los enums bien escritos, el mismo caso da REJECT +
    BLOCKER.

    Importa desde que el modo demo corre con modelos que no son Claude: los
    prompts piden el enum exacto, pero un modelo mas chico escribe BLOCKED.
    """

    TX = {"id": "TXN-00051", "payment_method": "Cripto", "fraud_score": 8, "amount_usd": 2095.9}

    @staticmethod
    def _resolver(veredictos):
        from api.app.services.resolution import ResolutionService

        return ResolutionService._determine_outcome(veredictos, TestUnVeredictoIlegibleNoEsUnVeredictoFavorable.TX)

    def test_los_enums_mal_escritos_no_aprueban(self):
        r = self._resolver([
            {"policy_code": "POL-EXC-003", "verdict": "BLOCKED", "reasoning": "cripto"},
            {"policy_code": "POL-FRD-001", "verdict": "FAILED", "reasoning": "score 8"},
        ])
        assert r["recommended_action"] != "APPROVE", "un veredicto ilegible aprobo el contracargo"
        assert r["requires_hitl"] is True

    def test_uno_solo_ilegible_ya_deriva_a_una_persona(self):
        """No hace falta que fallen todos: la evidencia ya no se puede leer."""
        r = self._resolver([
            {"policy_code": "POL-CB-001", "verdict": "PASS", "reasoning": "ok"},
            {"policy_code": "POL-FRD-001", "verdict": "si", "reasoning": "x"},
        ])
        assert r["requires_hitl"] is True

    def test_dice_cual_politica_no_se_pudo_leer(self):
        """Sin el codigo, quien revisa no sabe donde mirar."""
        r = self._resolver([{"policy_code": "POL-FRD-001", "verdict": "QUIZAS", "reasoning": "x"}])
        assert "POL-FRD-001" in r["hitl_reason"]

    def test_los_enums_bien_escritos_siguen_decidiendo(self):
        """El arreglo no puede volver paranoico al sistema."""
        r = self._resolver([
            {"policy_code": "POL-EXC-003", "verdict": "BLOCKER", "reasoning": "cripto"},
            {"policy_code": "POL-FRD-001", "verdict": "FAIL", "reasoning": "score 8"},
        ])
        assert r["recommended_action"] == "REJECT"
        assert r["risk_level"] == "BLOCKER"

    def test_todo_pass_sigue_aprobando(self):
        r = self._resolver([{"policy_code": "POL-CB-001", "verdict": "PASS", "reasoning": "ok"}])
        assert r["recommended_action"] == "APPROVE"


class TestLasAlertasSalenPorDondeVengaElCaso:
    """El pipeline directo resolvia sin dejar alerta; la ruta si la dejaba.

    `_emit_resolve_alerts` vivia en `routes/analyze.py`, asi que dependia de por
    donde entraba el caso. Verificado en la instancia publicada: TXN-00007 por
    el pipeline directo terminaba en PENDING_HITL y `GET /api/alerts/` devolvia
    `[]`; el mismo caso por `POST /api/analyze/resolve` si aparecia.

    Que un HITL figure o no en el log operativo segun el camino es un problema
    de auditoria, no de comodidad. Ahora se emite donde nace la resolucion, que
    es el unico punto por el que pasan los cuatro caminos.
    """

    @staticmethod
    def _servicio(anotadas):
        from api.app.services.resolution import ResolutionService

        return ResolutionService(alertas=anotadas.append)

    def test_un_bloqueante_deja_su_alerta(self):
        anotadas = []
        self._servicio(anotadas)._alertar({"risk_level": "BLOCKER"}, "TXN-00051")
        assert anotadas and anotadas[0]["event_type"] == "blocker_auto_reject"
        assert anotadas[0]["transaction_id"] == "TXN-00051"

    def test_un_caso_que_espera_una_persona_tambien(self):
        anotadas = []
        self._servicio(anotadas)._alertar({"risk_level": "HIGH", "requires_hitl": True}, "TXN-00042")
        assert anotadas and anotadas[0]["event_type"] == "hitl_required"

    def test_una_aprobacion_limpia_no_ensucia_el_log(self):
        anotadas = []
        self._servicio(anotadas)._alertar({"risk_level": "LOW", "requires_hitl": False}, "TXN-1")
        assert anotadas == []

    def test_sin_sumidero_no_se_rompe(self):
        """Es el caso de los tests, y no puede tumbar una resolucion."""
        from api.app.services.resolution import ResolutionService

        ResolutionService()._alertar({"risk_level": "BLOCKER"}, "TXN-1")

    def test_si_anotar_falla_la_resolucion_sigue(self):
        """Observar el sistema no puede ser una forma nueva de romperlo."""
        from api.app.services.resolution import ResolutionService

        def explota(_):
            raise RuntimeError("la base no responde")

        ResolutionService(alertas=explota)._alertar({"risk_level": "BLOCKER"}, "TXN-1")
