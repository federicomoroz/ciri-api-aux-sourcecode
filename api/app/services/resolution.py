"""
Service layer for resolution and judge operations.

Extracts orchestration logic from routes/analyze.py, keeping HTTP handlers thin.
LLM failures are re-raised — callers must handle errors explicitly.
"""

import json
import logging

from ..analysis.analyzer import Analyzer
from ..domain.constants import (
    BLOCKER_POLICY_CODES,
    FALLBACK_TX_ID,
    FRAUD_SCORE_DEFAULT,
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    GUARDRAIL_AUTO_CORRECTED_PREFIX,
    GUARDRAIL_HITL_REASON_GENERIC,
    GUARDRAIL_MAX_COMPENSATION_RATIO,
    GUARDRAIL_MAX_CONFIDENCE,
    GUARDRAIL_MIN_FAILS_FOR_WARNING,
    JUDGE_APPROVAL_THRESHOLD,
    LLM_MAX_CRITICAL_LOGS,
    RISK_FRAUD_SEVERE,
    RISK_HIGH_MIN_FAILS,
    TRACE_JUDGE,
    TRACE_RESOLVE,
)
from ..domain.context import CaseContext
from ..domain.enums import ResolutionOutcome, RiskLevel, Severity, VerdictType
from ..domain.models import JudgeEvaluationOutput, PolicyVerdictOutput, ResolutionOutput
from ..llm.client import LLMClient, LLMResult
from ..llm import prompts
from ..llm.parsing import validate_llm_output
from ..observability.tracer import Tracer
from ..rag.formatter import annotate_by_motivo, format_cases_for_prompt, format_policies_for_prompt

logger = logging.getLogger(__name__)


class ResolutionService:
    def __init__(self, llm: LLMClient, tracer: Tracer, llm_resolution: LLMClient | None = None):
        self.llm = llm
        self.llm_resolution = llm_resolution or llm
        self.tracer = tracer

    def resolve(self, ctx: CaseContext) -> dict:
        """Full resolution pipeline: policy eval -> log summary -> resolution synthesis -> guardrails.

        Raises on LLM failure — never produces incomplete resolutions silently.
        """
        tx_data, motivo, cliente_vip = ctx.transaction, ctx.motivo, ctx.cliente_vip
        policies, similar_cases, logs = ctx.policies, ctx.similar_cases, ctx.logs
        merchant_risk, client_history = ctx.merchant_risk, ctx.client_history

        tx_id = ctx.transaction_id or FALLBACK_TX_ID
        trace_id = self.tracer.trace(
            TRACE_RESOLVE,
            input={"transaction_id": tx_id, "motivo": motivo, "cliente_vip": cliente_vip},
            output={},
            metadata={"merchant": tx_data.get("merchant", ""), "amount_usd": tx_data.get("amount_usd", 0)},
        )

        policy_verdicts, eval_result = self._eval_policies(
            tx_data, policies, trace_id,
            merchant_risk=merchant_risk, client_history=client_history,
        )
        log_summary_text = self._summarize_logs(logs)

        # Deterministic outcome — code decides, LLM explains.
        outcome = self._determine_outcome(policy_verdicts, tx_data)
        precedent_summary = self._build_precedent_summary(
            similar_cases, motivo, tx_merchant=tx_data.get("merchant", ""),
        )
        outcome["precedent_summary"] = precedent_summary

        resolution, synth_result = self._synthesize_resolution(
            tx_data, policy_verdicts, similar_cases, log_summary_text,
            merchant_risk, client_history, motivo, cliente_vip, logs, trace_id,
            determined_outcome=outcome,
        )

        # Detectar la alucinacion ANTES de corregirla: una vez aplicado el
        # override determinista, lo que propuso el modelo ya no es observable.
        warnings = self._detect_divergence(resolution, outcome, policy_verdicts)

        # Override LLM decisions with deterministic values (always).
        resolution["policy_verdicts"] = policy_verdicts
        resolution["recommended_action"] = outcome["recommended_action"]
        resolution["risk_level"] = outcome["risk_level"]
        resolution["requires_hitl"] = outcome["requires_hitl"]
        resolution["precedent_summary"] = precedent_summary
        if outcome["hitl_reason"]:
            resolution["hitl_reason"] = outcome["hitl_reason"]

        warnings += self._validate_resolution(resolution, tx_data)
        usage = {
            "input_tokens": eval_result.input_tokens + synth_result.input_tokens,
            "output_tokens": eval_result.output_tokens + synth_result.output_tokens,
            "call_count": 2,
        }
        return {**resolution, "guardrail_warnings": warnings, "trace_id": trace_id, "_usage": usage}

    def _eval_policies(
        self,
        tx_data: dict,
        policies: list[dict],
        trace_id: str,
        merchant_risk: dict | None = None,
        client_history: dict | None = None,
    ) -> tuple[list[dict], LLMResult]:
        """Step 1: LLM policy evaluation. Raises on failure."""
        policies_formatted = format_policies_for_prompt(policies)
        sys_eval, usr_eval = prompts.v1_policy_eval.render(
            transaction=tx_data,
            policies_text=policies_formatted,
            policy_count=len(policies),
            merchant_risk=merchant_risk or {},
            client_history=client_history or {},
        )
        result = self.llm.complete(sys_eval, usr_eval, trace_id=trace_id)
        verdicts = validate_llm_output(result.text, PolicyVerdictOutput, [])
        verdicts = self._sanitize_verdicts(verdicts)
        return verdicts, result

    @staticmethod
    def _sanitize_verdicts(verdicts: list[dict]) -> list[dict]:
        """Downgrade invalid BLOCKER verdicts to FAIL.

        Only policies in BLOCKER_POLICY_CODES can produce legitimate BLOCKERs.
        Other BLOCKERs are LLM over-escalation (e.g. merchant suspension ≠ BLOCKER).
        """
        for v in verdicts:
            if (
                v.get("verdict") == VerdictType.BLOCKER
                and v.get("policy_code") not in BLOCKER_POLICY_CODES
            ):
                logger.warning(
                    "BLOCKER downgraded to FAIL for %s (not in BLOCKER_POLICY_CODES)",
                    v.get("policy_code"),
                )
                v["verdict"] = VerdictType.FAIL
                v["requires_human_review"] = True
        return verdicts

    def _synthesize_resolution(
        self,
        tx_data: dict,
        policy_verdicts: list[dict],
        similar_cases: list[dict],
        log_summary: str,
        merchant_risk: dict,
        client_history: dict,
        motivo: str | None,
        cliente_vip: bool,
        logs: list[dict],
        trace_id: str,
        determined_outcome: dict | None = None,
    ) -> tuple[dict, LLMResult]:
        """Step 4: LLM resolution synthesis. Raises on failure."""
        cases_formatted = format_cases_for_prompt(similar_cases, current_motivo=motivo)
        sys_res, usr_res = prompts.v1_resolution.render(
            transaction=tx_data,
            policy_verdicts=json.dumps(policy_verdicts, ensure_ascii=False, indent=2),
            similar_cases=cases_formatted,
            log_summary=log_summary,
            merchant_risk=merchant_risk,
            client_history=client_history,
            motivo=motivo,
            cliente_vip=cliente_vip,
            precedent_count=len(similar_cases),
            log_count=len(logs),
            determined_outcome=determined_outcome,
        )
        result = self.llm_resolution.complete(sys_res, usr_res, trace_id=trace_id)
        resolution = validate_llm_output(result.text, ResolutionOutput, {})
        return resolution, result

    def judge(self, resolution: dict, full_context: dict) -> dict:
        """LLM-as-Judge: evaluate resolution quality across 5 criteria.

        Raises on LLM failure — callers must handle errors explicitly.
        """
        tx_id = full_context.get("transaction", {}).get("id", FALLBACK_TX_ID)
        resolve_trace_id = resolution.get("trace_id", "")
        trace_id = self.tracer.trace(
            TRACE_JUDGE,
            input={"transaction_id": tx_id, "action": resolution.get("recommended_action")},
            output={},
            metadata={"confidence": resolution.get("confidence")},
        )

        # Strip internal metadata — Judge evaluates the corrected resolution, not the audit trail.
        # guardrail_warnings and guardrail-set hitl_reason mention original pre-correction
        # values (e.g. "Auto-corregido: REJECT sin BLOCKER...") which confuse the Judge LLM.
        _strip_keys = {"guardrail_warnings", "_usage", "trace_id"}
        judge_resolution = {k: v for k, v in resolution.items() if k not in _strip_keys}
        if str(judge_resolution.get("hitl_reason", "")).startswith(GUARDRAIL_AUTO_CORRECTED_PREFIX):
            judge_resolution["hitl_reason"] = GUARDRAIL_HITL_REASON_GENERIC

        system, user = prompts.v1_judge.render(
            full_context=full_context,
            resolution=judge_resolution,
        )
        llm_result = self.llm_resolution.complete(system, user, trace_id=trace_id)
        result = validate_llm_output(llm_result.text, JudgeEvaluationOutput, {})

        if "overall_score" not in result and "criteria" in result:
            scores = [float(v) for v in result["criteria"].values()]
            result["overall_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0
        if "approved" not in result:
            result["approved"] = result.get("overall_score", 0) >= JUDGE_APPROVAL_THRESHOLD

        if result.get("overall_score") is not None:
            self.tracer.score(trace_id, "judge_score", result["overall_score"])
            # Also attach score to the resolve trace so panel stats can find it
            if resolve_trace_id:
                self.tracer.score(resolve_trace_id, "judge_score", result["overall_score"])

        result["_usage"] = {
            "input_tokens": llm_result.input_tokens,
            "output_tokens": llm_result.output_tokens,
            "call_count": 1,
        }
        return result

    @staticmethod
    def _summarize_logs(logs: list[dict]) -> str:
        """Resume los logs para el prompt: conteos, patrones y eventos criticos.

        Los patrones los detecta `Analyzer.detect_error_patterns`, sin LLM. Es lo
        que convierte una lista de eventos sueltos en una senal aprovechable:
        "timeout sistematico del comercio" dice bastante mas que seis lineas
        repetidas de MERCHANT_NO_RESPONSE.
        """
        analysis = Analyzer.detect_error_patterns(logs)
        severity_counts = analysis["severity_counts"]
        text = (
            f"Total: {len(logs)} eventos | "
            f"ERROR: {severity_counts[Severity.ERROR]} | "
            f"WARN: {severity_counts[Severity.WARN]} | "
            f"INFO: {severity_counts[Severity.INFO]}\n"
        )
        if analysis["patterns"]:
            text += f"Patrones detectados: {', '.join(analysis['patterns'])}\n"
        for log in analysis["critical_events"][:LLM_MAX_CRITICAL_LOGS]:
            text += f"- [{log['severity']}] {log['event']}: {log['detail']}\n"
        return text

    # Que implica cada tipo de resolucion previa, para no dejarselo interpretar
    # al modelo. Se evalua en orden: la primera coincidencia gana.
    _IMPLICACION_POR_RESOLUCION: tuple[tuple[tuple[str, ...], str], ...] = (
        (("sin resolucion", "pendiente"),
         "caso similar permanece sin resolver — sugiere que este tipo de caso requiere "
         "investigacion adicional antes de decidir"),
        (("cerrado",),
         "caso similar fue cerrado sin resolucion explicita — riesgo de que el caso actual "
         "siga el mismo camino si no se investiga la causa raiz antes de decidir"),
        (("aprobado", "a favor"),
         "precedente fue aprobado — patron favorable al cliente para este tipo de caso"),
        (("rechazado", "denegado"),
         "precedente fue rechazado — patron desfavorable al cliente para este tipo de caso"),
        (("parcial",),
         "precedente resuelto con reembolso parcial — solucion intermedia para este tipo de caso"),
    )

    @staticmethod
    def _describe_precedent(
        case: dict,
        label: str | None,
        match_source: str | None,
        tx_merchant: str,
    ) -> str:
        """Una linea por precedente, con sus etiquetas y su implicacion."""
        case_merchant = case.get("merchant", "")
        motivo = case.get("motivo", "?")
        resolution = case.get("resolution", "?")

        tags = []
        if label:
            tags.append("[MOTIVO SIMILAR]")
        if tx_merchant and case_merchant and case_merchant.lower() == tx_merchant.lower():
            tags.append("[MISMO MERCHANT]")

        linea = (
            f"{case.get('case_id', '?')}{' ' + ' '.join(tags) if tags else ''}: "
            f"{motivo}, {resolution} en {case.get('resolution_days', '?')}d"
        )
        if case_merchant:
            linea += f", merchant={case_merchant}"
        if not label:
            return linea

        obs = case.get("observations", "")
        if obs:
            linea += f". Obs: {obs}"
        origen = (
            f" (match por {match_source}, motivo registrado: {motivo})"
            if match_source == "observaciones" else ""
        )
        linea += f". Relevancia: mismo patron de {label}{origen}"

        res_lower = resolution.lower()
        for claves, implicacion in ResolutionService._IMPLICACION_POR_RESOLUCION:
            if any(k in res_lower for k in claves):
                return f"{linea}. Nota: {implicacion}"
        return linea

    @staticmethod
    def _build_precedent_summary(
        similar_cases: list[dict],
        current_motivo: str | None,
        tx_merchant: str = "",
    ) -> str:
        """Build precedent_summary deterministically. No LLM involved.

        Extracts case_id, motivo, resolution, resolution_days from each case.
        Tags [MOTIVO SIMILAR] using synonym matching and sorts matches first.
        Tags [MISMO MERCHANT] when precedent merchant matches current transaction.
        """
        if not similar_cases:
            return "Sin precedentes relevantes."

        annotated = annotate_by_motivo(similar_cases, current_motivo)

        parts = [
            ResolutionService._describe_precedent(c, label, match_source, tx_merchant)
            for c, label, match_source in annotated
        ]

        # Deterministic pattern analysis across ALL precedents.
        outcomes_all = [c.get("resolution", "").lower() for c, _, _ in annotated]
        approved_all = sum(1 for o in outcomes_all if "aprobado" in o or "a favor" in o)
        rejected_all = sum(1 for o in outcomes_all if "rechazado" in o or "denegado" in o)
        total_all = len(annotated)
        matching = [(c, label) for c, label, _ in annotated if label is not None]

        # Strategic pattern implication.
        if approved_all > rejected_all:
            trend = "tendencia favorable al cliente"
        elif rejected_all > approved_all:
            trend = "tendencia desfavorable al cliente"
        else:
            trend = "sin tendencia clara"

        pattern = f"Patron: de {total_all} precedentes, {approved_all} aprobados, {rejected_all} rechazados — {trend}"
        if matching:
            approved_match = sum(
                1 for c, _ in matching
                if "aprobado" in c.get("resolution", "").lower()
                or "a favor" in c.get("resolution", "").lower()
            )
            pattern += f". Motivo similar: {len(matching)}/{total_all}, {approved_match} aprobados"
        parts.append(pattern)

        return " | ".join(parts)

    @staticmethod
    def _codigos(policy_verdicts: list[dict], *veredictos: str) -> list[str]:
        """Codigos de las politicas con alguno de esos veredictos."""
        return [
            v.get("policy_code", "?") for v in policy_verdicts
            if v.get("verdict") in veredictos
        ]

    @staticmethod
    def _nivel_de_riesgo(
        policy_verdicts: list[dict],
        has_blocker: bool,
        fail_count: int,
        fraud_score: int,
    ) -> tuple[str, str]:
        """(nivel, motivo). El motivo se escribe aca para que viaje con el nivel."""
        codigos_fallidos = ResolutionService._codigos(
            policy_verdicts, VerdictType.FAIL, VerdictType.BLOCKER,
        )

        if has_blocker:
            bloqueantes = ResolutionService._codigos(policy_verdicts, VerdictType.BLOCKER)
            return RiskLevel.BLOCKER, (
                f"Veredicto BLOCKER en {', '.join(bloqueantes)} (transaccion irreversible)"
            )

        if fail_count >= RISK_HIGH_MIN_FAILS or fraud_score < RISK_FRAUD_SEVERE:
            motivos = []
            if fail_count >= RISK_HIGH_MIN_FAILS:
                motivos.append(f"{fail_count} violaciones de politica ({', '.join(codigos_fallidos)})")
            if fraud_score < RISK_FRAUD_SEVERE:
                motivos.append(f"fraud_score={fraud_score} (umbral severo: {RISK_FRAUD_SEVERE})")
            if fraud_score >= FRAUD_SCORE_HIGH_RISK_THRESHOLD:
                # El riesgo viene de la politica, no del fraude: conviene decirlo.
                motivos.append(
                    f"fraud_score={fraud_score} indica bajo riesgo de fraude — "
                    f"riesgo HIGH es por violaciones de politica, no por fraude"
                )
            return RiskLevel.HIGH, f"HIGH por: {', '.join(motivos)}"

        if fail_count >= 1 or fraud_score < FRAUD_SCORE_HIGH_RISK_THRESHOLD:
            nota_fraude = (
                f" (fraud_score={fraud_score} seguro, riesgo es de politica)"
                if fraud_score >= FRAUD_SCORE_HIGH_RISK_THRESHOLD else ""
            )
            nota_codigos = f" ({', '.join(codigos_fallidos)})" if codigos_fallidos else ""
            return RiskLevel.MEDIUM, (
                f"MEDIUM por: {fail_count} violacion(es){nota_codigos}, "
                f"fraud_score={fraud_score}{nota_fraude}"
            )

        return RiskLevel.LOW, f"LOW: sin violaciones, fraud_score={fraud_score} (seguro)"

    @staticmethod
    def _accion(has_blocker: bool, fail_count: int, needs_human: bool) -> tuple[str, bool, str | None]:
        """(accion, requiere persona, motivo). Un BLOCKER se resuelve solo; una
        violacion sin BLOCKER siempre pasa por un analista."""
        if has_blocker:
            return ResolutionOutcome.REJECT, False, None
        if fail_count > 0:
            return ResolutionOutcome.PENDING_HITL, True, (
                f"{fail_count} violacion(es) de politica — requiere revision de analista"
            )
        if needs_human:
            return ResolutionOutcome.PENDING_HITL, True, (
                "Evaluacion de politicas requiere revision humana"
            )
        return ResolutionOutcome.APPROVE, False, None

    @staticmethod
    def _determine_outcome(policy_verdicts: list[dict], tx_data: dict) -> dict:
        """Accion y riesgo a partir de los veredictos. Sin LLM.

        Reglas:
        - Algun veredicto BLOCKER → REJECT + riesgo BLOCKER
        - Algun FAIL (sin BLOCKER) → PENDING_HITL + riesgo HIGH o MEDIUM
        - Algun requires_human_review → PENDING_HITL (red de seguridad)
        - Todo PASS/WARNING → APPROVE + riesgo LOW o MEDIUM
        """
        has_blocker = any(v.get("verdict") == VerdictType.BLOCKER for v in policy_verdicts)
        fail_count = len(
            ResolutionService._codigos(policy_verdicts, VerdictType.FAIL, VerdictType.BLOCKER)
        )
        needs_human = any(v.get("requires_human_review") is True for v in policy_verdicts)
        fraud_score = int(tx_data.get("fraud_score", FRAUD_SCORE_DEFAULT))

        risk_level, risk_reason = ResolutionService._nivel_de_riesgo(
            policy_verdicts, has_blocker, fail_count, fraud_score,
        )
        action, requires_hitl, hitl_reason = ResolutionService._accion(
            has_blocker, fail_count, needs_human,
        )

        return {
            "recommended_action": action,
            "risk_level": risk_level,
            "risk_reason": risk_reason,
            "requires_hitl": requires_hitl,
            "hitl_reason": hitl_reason,
        }

    @staticmethod
    def _detect_divergence(
        llm_proposal: dict,
        outcome: dict,
        policy_verdicts: list[dict],
    ) -> list[str]:
        """Compara lo que propuso el modelo con lo que decidio el codigo.

        Se ejecuta antes del override determinista, que es la unica ventana en la
        que la propuesta del modelo todavia existe. Las tres divergencias que
        detecta son contradicciones con la evidencia, no diferencias de criterio:
        el modelo afirma algo que sus propios veredictos desmienten.

        No corrige nada — de eso ya se encarga el override. Deja constancia, que
        es lo que un auditor necesita para saber que el modelo se equivoco.
        """
        warnings = []
        has_blocker = any(
            v.get("verdict") == VerdictType.BLOCKER for v in policy_verdicts
        )
        propuesta = llm_proposal.get("recommended_action")
        riesgo = llm_proposal.get("risk_level")

        if propuesta == ResolutionOutcome.APPROVE and has_blocker:
            warnings.append(
                f"GUARDRAIL: el modelo propuso APPROVE con un veredicto BLOCKER activo — "
                f"corregido a {outcome['recommended_action']} (posible alucinacion)"
            )
        if riesgo == RiskLevel.BLOCKER and not has_blocker:
            warnings.append(
                f"GUARDRAIL: el modelo propuso risk_level=BLOCKER sin veredictos BLOCKER reales — "
                f"corregido a {outcome['risk_level']}"
            )
        if propuesta == ResolutionOutcome.REJECT and not has_blocker:
            warnings.append(
                f"GUARDRAIL: el modelo propuso REJECT sin veredictos BLOCKER — "
                f"corregido a {outcome['recommended_action']} (requiere revision humana)"
            )

        for w in warnings:
            logger.warning("%s", w)
        return warnings

    @staticmethod
    def _validate_resolution(resolution: dict, transaction: dict) -> list[str]:
        """Guardrails sobre los campos que el override no toca.

        La accion y el nivel de riesgo los fija el codigo, asi que no hay nada que
        validar ahi. Estos dos campos, en cambio, salen del modelo tal cual.
        """
        warnings = []

        comp = resolution.get("compensation_amount_usd", 0)
        tx_amount = transaction.get("amount_usd", 0)
        if comp > tx_amount * GUARDRAIL_MAX_COMPENSATION_RATIO and tx_amount > 0:
            warnings.append(
                f"GUARDRAIL: Compensacion USD {comp:.2f} excede el monto original USD {tx_amount:.2f} en >10%"
            )

        fail_count = sum(
            1 for v in resolution.get("policy_verdicts", [])
            if v.get("verdict") in (VerdictType.FAIL, VerdictType.BLOCKER)
        )
        if resolution.get("confidence", 0) > GUARDRAIL_MAX_CONFIDENCE and fail_count >= GUARDRAIL_MIN_FAILS_FOR_WARNING:
            warnings.append(
                f"GUARDRAIL: Confianza excesiva ({resolution['confidence']}) con {fail_count} violaciones de politica"
            )

        return warnings
