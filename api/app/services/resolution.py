"""
Service layer for resolution and judge operations.

Extracts orchestration logic from routes/analyze.py, keeping HTTP handlers thin.
Integrates semantic cache (Qdrant) to skip LLM calls for near-identical queries.
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
)
from ..domain.enums import ResolutionOutcome, RiskLevel, Severity, VerdictType
from ..llm.client import LLMClient
from ..llm import prompts
from ..llm.parsing import parse_json_safely
from ..observability.tracer import Tracer
from ..rag.retriever import QdrantRetriever

logger = logging.getLogger(__name__)


class ResolutionService:
    def __init__(
        self,
        llm: LLMClient,
        tracer: Tracer,
        retriever: QdrantRetriever | None = None,
        cache_enabled: bool = True,
    ):
        self.llm = llm
        self.tracer = tracer
        self.retriever = retriever
        self.cache_enabled = cache_enabled and retriever is not None

    def _build_cache_key(self, tx_data: dict, motivo: str | None, cliente_vip: bool) -> str:
        """Deterministic cache key from the immutable inputs of a resolve call."""
        return (
            f"resolve:{tx_data.get('id', '')}|{motivo or ''}|{cliente_vip}|"
            f"{tx_data.get('merchant', '')}|{tx_data.get('amount_usd', 0)}|"
            f"{tx_data.get('fraud_score', 0)}|{tx_data.get('payment_method', '')}"
        )

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
        """Full resolution pipeline: policy eval → log summary → resolution synthesis → guardrails.
        Uses semantic cache to skip LLM calls for near-identical requests."""
        tx_id = tx_data.get("id", "unknown")

        # Semantic cache check — skip entire LLM pipeline if near-identical query was seen
        cache_key = self._build_cache_key(tx_data, motivo, cliente_vip)
        if self.cache_enabled:
            cached = self.retriever.check_semantic_cache(cache_key)
            if cached:
                logger.info("Semantic cache HIT for %s", tx_id)
                if isinstance(cached, dict):
                    cached["_cache_hit"] = True
                    return cached
                # cached is a string (legacy) — parse it
                result = parse_json_safely(cached, {})
                if result:
                    result["_cache_hit"] = True
                    return result

        trace_id = self.tracer.trace(
            "resolve_chargeback",
            input={"transaction_id": tx_id, "motivo": motivo, "cliente_vip": cliente_vip},
            output={},
            metadata={"merchant": tx_data.get("merchant", ""), "amount_usd": tx_data.get("amount_usd", 0)},
        )

        # Step 1: Policy evaluation
        policies_formatted = "\n\n".join(
            f"**{p.get('code', 'N/A')}** — {p.get('category', '')}\n"
            f"Nombre: {p.get('name', '')}\n"
            f"Descripcion: {p.get('description', '')}\n"
            f"Referencia: {p.get('reference', '')}"
            for p in policies
        )
        sys_eval, usr_eval = prompts.v1_policy_eval.render(
            transaction=tx_data,
            policies_text=policies_formatted,
            policy_count=len(policies),
        )
        policy_verdicts = parse_json_safely(self.llm.complete(sys_eval, usr_eval, trace_id=trace_id), [])

        # Step 2: Log summary
        log_summary_text = self._summarize_logs(logs)

        # Step 3: Format precedents
        cases_formatted = "\n\n".join(
            f"Caso {c.get('case_id', '?')}: {c.get('motivo', '?')} → {c.get('resolution', '?')} "
            f"({c.get('resolution_days', '?')} dias). Comercio: {c.get('merchant', '?')}, "
            f"Monto: USD {c.get('amount_usd', 0):.2f}, Score: {c.get('fraud_score', '?')}"
            for c in similar_cases
        ) or "(Sin precedentes similares)"

        # Step 4: Resolution synthesis
        sys_res, usr_res = prompts.v1_resolution.render(
            transaction=tx_data,
            policy_verdicts=json.dumps(policy_verdicts, ensure_ascii=False, indent=2),
            similar_cases=cases_formatted,
            log_summary=log_summary_text,
            merchant_risk=merchant_risk,
            client_history=client_history,
            motivo=motivo,
            cliente_vip=cliente_vip,
            precedent_count=len(similar_cases),
            log_count=len(logs),
        )
        resolution = parse_json_safely(self.llm.complete(sys_res, usr_res, trace_id=trace_id), {})

        if "policy_verdicts" not in resolution or not resolution["policy_verdicts"]:
            resolution["policy_verdicts"] = policy_verdicts

        # Step 5: Guardrails
        warnings = self._validate_resolution(resolution, tx_data)
        result = {**resolution, "guardrail_warnings": warnings, "trace_id": trace_id}

        # Store in semantic cache for future near-identical queries
        if self.cache_enabled:
            try:
                self.retriever.store_in_cache(cache_key, result)
                logger.info("Semantic cache STORE for %s", tx_id)
            except Exception as e:
                logger.warning("Failed to store in semantic cache: %s", e)

        return result

    def judge(self, resolution: dict, full_context: dict) -> dict:
        """LLM-as-Judge: evaluate resolution quality across 5 criteria."""
        tx_id = full_context.get("transaction", {}).get("id", "unknown")
        trace_id = self.tracer.trace(
            "judge_resolution",
            input={"transaction_id": tx_id, "action": resolution.get("recommended_action")},
            output={},
            metadata={"confidence": resolution.get("confidence")},
        )

        system, user = prompts.v1_judge.render(
            full_context=full_context,
            resolution=resolution,
        )
        result = parse_json_safely(self.llm.complete(system, user, trace_id=trace_id), {})

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
            f"ERROR: {severity_counts['ERROR']} | "
            f"WARN: {severity_counts['WARN']} | "
            f"INFO: {severity_counts['INFO']}\n"
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
