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
    FRAUD_SCORE_DEFAULT,
    FRAUD_SCORE_HIGH_RISK_THRESHOLD,
    GUARDRAIL_MAX_COMPENSATION_RATIO,
    GUARDRAIL_MAX_CONFIDENCE,
    GUARDRAIL_MIN_FAILS_FOR_WARNING,
    JUDGE_APPROVAL_THRESHOLD,
    LLM_MAX_CRITICAL_LOGS,
    RISK_FRAUD_SEVERE,
    RISK_HIGH_MIN_FAILS,
    TRACE_RESOLVE,
    TRACE_JUDGE,
    FALLBACK_TX_ID,
)
from ..domain.enums import ResolutionOutcome, RiskLevel, Severity, VerdictType
from ..domain.models import JudgeEvaluationOutput, PolicyVerdictOutput, ResolutionOutput
from ..llm.client import LLMClient, LLMResult
from ..llm import prompts
from ..llm.parsing import validate_llm_output
from ..observability.tracer import Tracer
from ..rag.formatter import format_cases_for_prompt, format_policies_for_prompt

logger = logging.getLogger(__name__)


class ResolutionService:
    def __init__(self, llm: LLMClient, tracer: Tracer):
        self.llm = llm
        self.tracer = tracer

    def resolve(
        self,
        tx_data: dict,
        policies: list[dict],
        similar_cases: list[dict],
        logs: list[dict],
        merchant_risk: dict,
        client_history: dict,
        motivo: str | None,
        cliente_vip: bool,
    ) -> dict:
        """Full resolution pipeline: policy eval -> log summary -> resolution synthesis -> guardrails.

        Raises on LLM failure — never produces incomplete resolutions silently.
        """
        tx_id = tx_data.get("id", FALLBACK_TX_ID)
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

        resolution, synth_result = self._synthesize_resolution(
            tx_data, policy_verdicts, similar_cases, log_summary_text,
            merchant_risk, client_history, motivo, cliente_vip, logs, trace_id,
            determined_outcome=outcome,
        )

        # Override LLM decisions with deterministic values (always).
        resolution["policy_verdicts"] = policy_verdicts
        resolution["recommended_action"] = outcome["recommended_action"]
        resolution["risk_level"] = outcome["risk_level"]
        resolution["requires_hitl"] = outcome["requires_hitl"]
        if outcome["hitl_reason"]:
            resolution["hitl_reason"] = outcome["hitl_reason"]

        warnings = self._validate_resolution(resolution, tx_data)
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
        result = self.llm.complete(sys_res, usr_res, trace_id=trace_id)
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
        if str(judge_resolution.get("hitl_reason", "")).startswith("Auto-corregido"):
            judge_resolution["hitl_reason"] = "Requiere revision de analista antes de decision final"

        system, user = prompts.v1_judge.render(
            full_context=full_context,
            resolution=judge_resolution,
        )
        llm_result = self.llm.complete(system, user, trace_id=trace_id)
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
        severity_counts = Analyzer.count_severities(logs)
        text = (
            f"Total: {len(logs)} eventos | "
            f"ERROR: {severity_counts[Severity.ERROR]} | "
            f"WARN: {severity_counts[Severity.WARN]} | "
            f"INFO: {severity_counts[Severity.INFO]}\n"
        )
        if logs:
            critical = [log for log in logs if log.get("severity") in (Severity.ERROR, Severity.WARN)]
            for log in critical[:LLM_MAX_CRITICAL_LOGS]:
                text += f"- [{log['severity']}] {log['event']}: {log['detail']}\n"
        return text

    @staticmethod
    def _determine_outcome(policy_verdicts: list[dict], tx_data: dict) -> dict:
        """Deterministic action/risk from policy verdicts. No LLM involved.

        Rules:
        - Any BLOCKER verdict → REJECT + risk BLOCKER
        - Any FAIL (no BLOCKER) → PENDING_HITL + risk HIGH or MEDIUM
        - Any requires_human_review=true → PENDING_HITL (safety net)
        - All PASS/WARNING → APPROVE + risk LOW or MEDIUM
        """
        has_blocker = any(
            v.get("verdict") == VerdictType.BLOCKER for v in policy_verdicts
        )
        fail_count = sum(
            1 for v in policy_verdicts
            if v.get("verdict") in (VerdictType.FAIL, VerdictType.BLOCKER)
        )
        needs_human = any(
            v.get("requires_human_review") is True for v in policy_verdicts
        )
        fraud_score = int(tx_data.get("fraud_score", FRAUD_SCORE_DEFAULT))

        # ── Risk level ──
        if has_blocker:
            risk_level = RiskLevel.BLOCKER
            risk_reason = "Veredicto BLOCKER presente (transaccion irreversible)"
        elif fail_count >= RISK_HIGH_MIN_FAILS or fraud_score < RISK_FRAUD_SEVERE:
            risk_level = RiskLevel.HIGH
            reasons = []
            if fail_count >= RISK_HIGH_MIN_FAILS:
                reasons.append(f"{fail_count} violaciones de politica")
            if fraud_score < RISK_FRAUD_SEVERE:
                reasons.append(f"fraud_score={fraud_score} (umbral severo: {RISK_FRAUD_SEVERE})")
            risk_reason = f"HIGH por: {', '.join(reasons)}"
        elif fail_count >= 1 or fraud_score < FRAUD_SCORE_HIGH_RISK_THRESHOLD:
            risk_level = RiskLevel.MEDIUM
            risk_reason = f"MEDIUM por: {fail_count} violacion(es), fraud_score={fraud_score}"
        else:
            risk_level = RiskLevel.LOW
            risk_reason = f"LOW: sin violaciones, fraud_score={fraud_score} (seguro)"

        # ── Action ──
        if has_blocker:
            action = ResolutionOutcome.REJECT
            requires_hitl = False
            hitl_reason = None
        elif fail_count > 0 or needs_human:
            action = ResolutionOutcome.PENDING_HITL
            requires_hitl = True
            if fail_count > 0:
                hitl_reason = (
                    f"{fail_count} violacion(es) de politica — requiere revision de analista"
                )
            else:
                hitl_reason = "Evaluacion de politicas requiere revision humana"
        else:
            action = ResolutionOutcome.APPROVE
            requires_hitl = False
            hitl_reason = None

        return {
            "recommended_action": action,
            "risk_level": risk_level,
            "risk_reason": risk_reason,
            "requires_hitl": requires_hitl,
            "hitl_reason": hitl_reason,
        }

    @staticmethod
    def _validate_resolution(resolution: dict, transaction: dict) -> list[str]:
        """Post-LLM guardrails. Returns list of warning strings. Critical violations are auto-corrected."""
        warnings = []

        has_blocker = any(
            v.get("verdict") == VerdictType.BLOCKER
            for v in resolution.get("policy_verdicts", [])
        )
        if resolution.get("recommended_action") == ResolutionOutcome.APPROVE and has_blocker:
            warnings.append(
                "GUARDRAIL: APPROVE con BLOCKER activo — auto-corregido a REJECT (posible alucinacion)"
            )
            resolution["recommended_action"] = ResolutionOutcome.REJECT
            resolution["risk_level"] = RiskLevel.BLOCKER
            resolution["requires_hitl"] = False

        if resolution.get("risk_level") == RiskLevel.BLOCKER and not has_blocker:
            warnings.append(
                "GUARDRAIL: risk_level=BLOCKER sin veredictos BLOCKER reales — auto-corregido a HIGH + PENDING_HITL"
            )
            resolution["risk_level"] = RiskLevel.HIGH
            resolution["requires_hitl"] = True
            if resolution.get("recommended_action") == ResolutionOutcome.REJECT:
                resolution["recommended_action"] = ResolutionOutcome.PENDING_HITL
                resolution["hitl_reason"] = "Auto-corregido: REJECT sin BLOCKER requiere confirmacion de analista"

        if resolution.get("recommended_action") == ResolutionOutcome.REJECT and not has_blocker:
            warnings.append(
                "GUARDRAIL: REJECT sin veredictos BLOCKER — auto-corregido a PENDING_HITL (requiere revision humana)"
            )
            resolution["recommended_action"] = ResolutionOutcome.PENDING_HITL
            resolution["requires_hitl"] = True

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
