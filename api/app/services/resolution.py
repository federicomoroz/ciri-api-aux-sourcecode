"""
Service layer for resolution and judge operations.

Extracts orchestration logic from routes/analyze.py, keeping HTTP handlers thin.
LLM failures are re-raised — callers must handle errors explicitly.
"""

import json
import logging

from ..analysis.analyzer import Analyzer
from ..domain.constants import (
    GUARDRAIL_MAX_COMPENSATION_RATIO,
    GUARDRAIL_MAX_CONFIDENCE,
    GUARDRAIL_MIN_FAILS_FOR_WARNING,
    JUDGE_APPROVAL_THRESHOLD,
    LLM_MAX_CRITICAL_LOGS,
    TRACE_RESOLVE,
    TRACE_JUDGE,
    TRACE_LLM_CALL,
    TRACE_FEEDBACK,
    TRACE_FEEDBACK_SCORE,
    FEEDBACK_STATUS_RECORDED,
    FEEDBACK_CASE_ID_PREFIX,
    FALLBACK_TX_ID,
)
from ..domain.enums import ResolutionOutcome, RiskLevel, Severity, VerdictType
from ..domain.models import JudgeEvaluationOutput, PolicyVerdictOutput, ResolutionOutput
from ..llm.client import LLMClient
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

        policy_verdicts = self._eval_policies(tx_data, policies, trace_id)
        log_summary_text = self._summarize_logs(logs)
        resolution = self._synthesize_resolution(
            tx_data, policy_verdicts, similar_cases, log_summary_text,
            merchant_risk, client_history, motivo, cliente_vip, logs, trace_id,
        )

        if "policy_verdicts" not in resolution or not resolution["policy_verdicts"]:
            resolution["policy_verdicts"] = policy_verdicts

        warnings = self._validate_resolution(resolution, tx_data)
        return {**resolution, "guardrail_warnings": warnings, "trace_id": trace_id}

    def _eval_policies(self, tx_data: dict, policies: list[dict], trace_id: str) -> list[dict]:
        """Step 1: LLM policy evaluation. Raises on failure."""
        policies_formatted = format_policies_for_prompt(policies)
        sys_eval, usr_eval = prompts.v1_policy_eval.render(
            transaction=tx_data,
            policies_text=policies_formatted,
            policy_count=len(policies),
        )
        return validate_llm_output(self.llm.complete(sys_eval, usr_eval, trace_id=trace_id), PolicyVerdictOutput, [])

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
    ) -> dict:
        """Step 4: LLM resolution synthesis. Raises on failure."""
        cases_formatted = format_cases_for_prompt(similar_cases)
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
        )
        return validate_llm_output(self.llm.complete(sys_res, usr_res, trace_id=trace_id), ResolutionOutput, {})

    def judge(self, resolution: dict, full_context: dict) -> dict:
        """LLM-as-Judge: evaluate resolution quality across 5 criteria.

        Raises on LLM failure — callers must handle errors explicitly.
        """
        tx_id = full_context.get("transaction", {}).get("id", FALLBACK_TX_ID)
        trace_id = self.tracer.trace(
            TRACE_JUDGE,
            input={"transaction_id": tx_id, "action": resolution.get("recommended_action")},
            output={},
            metadata={"confidence": resolution.get("confidence")},
        )

        system, user = prompts.v1_judge.render(
            full_context=full_context,
            resolution=resolution,
        )
        result = validate_llm_output(self.llm.complete(system, user, trace_id=trace_id), JudgeEvaluationOutput, {})

        if "overall_score" not in result and "criteria" in result:
            scores = list(result["criteria"].values())
            result["overall_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0
        if "approved" not in result:
            result["approved"] = result.get("overall_score", 0) >= JUDGE_APPROVAL_THRESHOLD

        if result.get("overall_score"):
            self.tracer.score(trace_id, "judge_score", result["overall_score"])

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
