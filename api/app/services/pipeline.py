"""
Direct analysis pipeline — mirrors the n8n explicit workflow without n8n.

Extracts the 9-step orchestration from routes/panel.py into a proper service.
"""

import logging
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from ..analysis.analyzer import Analyzer
from ..data.db import Database, cache_key
from ..domain.constants import (
    LLM_PRICING,
    LLM_PRICING_PER_MTOK,
    PIPELINE_MAX_WORKERS,
    PIPELINE_THREAD_TIMEOUT_S,
)
from ..domain.enums import VerdictType
from ..domain.models import AnalyzeRequest
from ..rag.retriever import QdrantRetriever
from ..reports.generator import ReportGenerator
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

# Type alias for streaming events
StreamEvent = tuple[str, dict]


@dataclass
class _PipelineContext:
    """Gathered context shared between run() and run_streaming()."""

    tx: dict
    logs: list[dict]
    policies: list[dict]
    similar_cases: list[dict]
    merchant_risk: dict
    client_history: dict


class PipelineService:
    """Orchestrates the full chargeback analysis pipeline (direct mode)."""

    def __init__(
        self,
        db: Database,
        retriever: QdrantRetriever,
        analyzer: Analyzer,
        resolution_svc: ResolutionService,
        report_gen: ReportGenerator,
    ):
        self.db = db
        self.retriever = retriever
        self.analyzer = analyzer
        self.resolution_svc = resolution_svc
        self.report_gen = report_gen

    # ── Shared helpers ──────────────────────────────────────────────────

    def _submit_context_futures(self, executor: ThreadPoolExecutor, tx: dict, req: AnalyzeRequest) -> dict:
        """Submit all parallel context-gathering tasks. Returns {future: name}."""
        payment_method = tx.get("payment_method", "")
        country = tx.get("country", "")
        fraud_score = int(tx.get("fraud_score", 0))

        return {
            executor.submit(self.db.get_logs_for_transaction, req.transaction_id): "logs",
            executor.submit(
                self.retriever.search_policies_and_cases,
                motivo=req.motivo,
                channel=tx.get("channel", ""),
                payment_method=payment_method,
                fraud_score=fraud_score,
                country=country,
                merchant=tx.get("merchant", ""),
                amount=float(tx.get("amount_usd", 0)),
            ): "rag",
            executor.submit(
                self.analyzer.merchant_risk_profile, tx.get("merchant", ""),
            ): "merchant",
            executor.submit(
                self.analyzer.client_flags, tx.get("client_id", ""),
            ): "client",
        }

    def _resolve(self, ctx: _PipelineContext, req: AnalyzeRequest) -> dict:
        """Run resolve LLM call."""
        return self.resolution_svc.resolve(
            tx_data=ctx.tx,
            policies=ctx.policies,
            similar_cases=ctx.similar_cases,
            logs=ctx.logs,
            merchant_risk=ctx.merchant_risk,
            client_history=ctx.client_history,
            motivo=req.motivo,
            cliente_vip=req.cliente_vip,
        )

    def _judge(self, ctx: _PipelineContext, resolution: dict, req: AnalyzeRequest) -> dict:
        """Run judge LLM call."""
        return self.resolution_svc.judge(
            resolution=resolution,
            full_context={
                "transaction": ctx.tx,
                "motivo": req.motivo,
                "policies": ctx.policies,
                "similar_cases": ctx.similar_cases,
                "merchant_risk": ctx.merchant_risk,
                "client_history": ctx.client_history,
                "logs": ctx.logs,
            },
        )

    def _build_report_data(
        self,
        ctx: _PipelineContext,
        resolution: dict,
        judge: dict,
        txn_id: str,
    ) -> dict:
        """Build the report data dict shared by both run paths."""
        return {
            "transaction": ctx.tx,
            "resolution": resolution,
            "judge_evaluation": judge,
            "agent_analysis": (
                f"Pipeline directo — {txn_id}: {ctx.tx.get('merchant', '')}, "
                f"USD {ctx.tx.get('amount_usd', '')}, canal {ctx.tx.get('channel', '')}, "
                f"país {ctx.tx.get('country', '')}, score fraude {ctx.tx.get('fraud_score', '')}."
            ),
            "merchant_risk": ctx.merchant_risk,
            "client_profile": ctx.client_history,
            "logs": ctx.logs,
            "policies_evaluated": resolution.get("policy_verdicts", []),
            "similar_cases": ctx.similar_cases,
            "hitl_decision": None,
            "cache_hit": False,
            "guardrail_warnings": resolution.get("guardrail_warnings", []),
        }

    # ── Public methods ──────────────────────────────────────────────────

    def run(self, req: AnalyzeRequest, model_name: str = "") -> tuple[str, dict]:
        """Execute the 9-step pipeline. Returns (html, usage_dict)."""
        txn_id = req.transaction_id

        # Step 0 — cache check
        key = cache_key(txn_id, req.cliente_vip)
        cached_html = self.db.get_cached_report(key)
        if cached_html:
            logger.info("Pipeline cache HIT for %s", txn_id)
            return cached_html, {"cache_hit": True}

        # Step 1 — lookup transaction
        tx = self.db.get_transaction(txn_id)
        if not tx:
            raise ValueError(f"Transaction {txn_id} not found in database.")

        # Steps 2-6 — parallel context gathering.
        # Los resultados se recogen DENTRO del with: al salir, el executor hace
        # shutdown(wait=True) y espera sin limite. Con la recoleccion afuera, el
        # timeout no protegia de nada porque los futures ya habian terminado.
        futures_results = {}
        with ThreadPoolExecutor(max_workers=PIPELINE_MAX_WORKERS) as executor:
            futures = self._submit_context_futures(executor, tx, req)
            for future in as_completed(futures, timeout=PIPELINE_THREAD_TIMEOUT_S):
                futures_results[futures[future]] = future.result()

        policies, similar_cases = futures_results["rag"]
        ctx = _PipelineContext(
            tx=tx,
            logs=futures_results["logs"],
            policies=policies,
            similar_cases=similar_cases,
            merchant_risk=futures_results["merchant"],
            client_history=futures_results["client"],
        )

        # Steps 7+8 — resolve + judge
        resolution = self._resolve(ctx, req)
        judge = self._judge(ctx, resolution, req)

        # Step 9 — html report
        report_data = self._build_report_data(ctx, resolution, judge, txn_id)
        html = self.report_gen.render(report_data)
        self._cache_report(key, html, txn_id)
        usage = self._aggregate_usage(resolution, judge, model_name)
        return html, usage

    def run_streaming(
        self, req: AnalyzeRequest, model_name: str = "",
    ) -> Generator[StreamEvent, None, None]:
        """Execute the pipeline yielding (step, data) events for SSE streaming."""
        txn_id = req.transaction_id
        yield ("start", {"transaction_id": txn_id})

        # Cache check
        key = cache_key(txn_id, req.cliente_vip)
        cached_html = self.db.get_cached_report(key)
        if cached_html:
            yield ("done", {"html": cached_html, "usage": {"cache_hit": True}})
            return
        yield ("cache_check", {"hit": False})

        # Transaction lookup
        tx = self.db.get_transaction(txn_id)
        if not tx:
            yield ("error", {"message": f"Transaction {txn_id} not found in database."})
            return
        yield ("transaction", {
            "merchant": tx.get("merchant", ""),
            "amount": float(tx.get("amount_usd", 0)),
            "country": tx.get("country", ""),
            "fraud_score": int(tx.get("fraud_score", 0)),
            "payment_method": tx.get("payment_method", ""),
        })

        # Parallel context gathering — emit events as each thread completes
        logs = []
        policies = []
        similar_cases = []
        merchant_risk = {}
        client_history = {}

        with ThreadPoolExecutor(max_workers=PIPELINE_MAX_WORKERS) as executor:
            futures = self._submit_context_futures(executor, tx, req)
            for future in as_completed(futures, timeout=PIPELINE_THREAD_TIMEOUT_S):
                name = futures[future]
                try:
                    result = future.result()
                except Exception:
                    logger.error("Context gathering failed for '%s'", name, exc_info=True)
                    yield ("error", {"message": f"Failed to gather {name}"})
                    return
                if name == "logs":
                    logs = result
                    yield ("logs", {"count": len(logs)})
                elif name == "rag":
                    policies, similar_cases = result
                    yield ("policies", {"count": len(policies)})
                    yield ("cases", {"count": len(similar_cases)})
                elif name == "merchant":
                    merchant_risk = result
                    yield ("merchant_risk", {
                        "cb_ratio": merchant_risk.get("cb_ratio", 0),
                        "flags": merchant_risk.get("flags", []),
                    })
                elif name == "client":
                    client_history = result
                    yield ("client_flags", {
                        "total_chargebacks": client_history.get("total_chargebacks", 0),
                        "flags": client_history.get("flags", []),
                    })

        ctx = _PipelineContext(
            tx=tx, logs=logs, policies=policies, similar_cases=similar_cases,
            merchant_risk=merchant_risk, client_history=client_history,
        )

        # Resolve (LLM)
        yield ("resolving", {})
        resolution = self._resolve(ctx, req)
        verdicts = resolution.get("policy_verdicts", [])
        yield ("resolved", {
            "action": resolution.get("recommended_action", ""),
            "risk_level": resolution.get("risk_level", ""),
            "verdicts": len(verdicts),
            "blockers": sum(1 for v in verdicts if v.get("verdict") == VerdictType.BLOCKER),
            "fails": sum(1 for v in verdicts if v.get("verdict") == VerdictType.FAIL),
            "warnings": len(resolution.get("guardrail_warnings", [])),
        })

        # Judge (LLM)
        yield ("judging", {})
        judge = self._judge(ctx, resolution, req)
        yield ("judged", {
            "score": judge.get("overall_score", 0),
            "approved": judge.get("approved", False),
        })

        # Render HTML report
        report_data = self._build_report_data(ctx, resolution, judge, txn_id)
        html = self.report_gen.render(report_data)
        self._cache_report(key, html, txn_id)
        usage = self._aggregate_usage(resolution, judge, model_name)
        yield ("done", {"html": html, "usage": usage})

    def _cache_report(self, key: str, html: str, txn_id: str) -> None:
        """Guarda el informe para que la proxima consulta del mismo caso no pague LLM.

        Best-effort: si el cache falla, el informe ya esta generado y no hay razon
        para tirar la respuesta.
        """
        try:
            self.db.store_cached_report(key, html)
        except Exception:
            logger.warning("No se pudo cachear el informe de %s", txn_id, exc_info=True)

    @staticmethod
    def _aggregate_usage(resolution: dict, judge: dict, model_name: str) -> dict:
        """Compute total tokens and cost from resolve + judge LLM calls."""
        resolve_usage = resolution.get("_usage", {})
        judge_usage = judge.get("_usage", {})
        total_in = resolve_usage.get("input_tokens", 0) + judge_usage.get("input_tokens", 0)
        total_out = resolve_usage.get("output_tokens", 0) + judge_usage.get("output_tokens", 0)
        total_calls = resolve_usage.get("call_count", 0) + judge_usage.get("call_count", 0)

        cost_usd = 0.0
        for key, (in_rate, out_rate) in LLM_PRICING.items():
            if key in model_name.lower():
                cost_usd = total_in * in_rate / LLM_PRICING_PER_MTOK + total_out * out_rate / LLM_PRICING_PER_MTOK
                break

        return {
            "input_tokens": total_in,
            "output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "call_count": total_calls,
            "cost_usd": round(cost_usd, 6),
            "model": model_name,
        }
