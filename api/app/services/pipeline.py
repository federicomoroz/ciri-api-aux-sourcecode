"""
Direct analysis pipeline — mirrors the n8n explicit workflow without n8n.

Extracts the 9-step orchestration from routes/panel.py into a proper service.
"""

import json
import logging
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..analysis.analyzer import Analyzer
from ..data.db import Database
from ..domain.constants import LLM_PRICING, LLM_PRICING_PER_MTOK
from ..domain.models import AnalyzeRequest
from ..rag.retriever import QdrantRetriever
from ..reports.generator import ReportGenerator
from ..services.resolution import ResolutionService

logger = logging.getLogger(__name__)

# Type alias for streaming events
StreamEvent = tuple[str, dict]


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

    def run(self, req: AnalyzeRequest, model_name: str = "") -> tuple[str, dict]:
        """Execute the 9-step pipeline. Returns (html, usage_dict)."""
        txn_id = req.transaction_id

        # Step 0 — cache check (mirrors n8n §1 "Verificar Caché")
        cache_key = f"{txn_id}|{req.cliente_vip}"
        cached_html = self.db.get_cached_report(cache_key)
        if cached_html:
            logger.info("Pipeline cache HIT for %s", txn_id)
            return cached_html, {"cache_hit": True}

        # Step 1 — lookup_transaction
        tx = self.db.get_transaction(txn_id)
        if not tx:
            raise ValueError(f"Transaction {txn_id} not found in database.")

        # Steps 2-6 — parallel context gathering
        # All steps depend only on tx + req, not on each other.
        # Policies + cases use batched embedding (1 Voyage API call instead of 2).
        payment_method = tx.get("payment_method", "")
        country = tx.get("country", "")
        fraud_score = int(tx.get("fraud_score", 0))

        with ThreadPoolExecutor(max_workers=4) as executor:
            f_logs = executor.submit(self.db.get_logs_for_transaction, txn_id)
            f_rag = executor.submit(
                self.retriever.search_policies_and_cases,
                motivo=req.motivo,
                channel=tx.get("channel", ""),
                payment_method=payment_method,
                fraud_score=fraud_score,
                country=country,
                merchant=tx.get("merchant", ""),
                amount=float(tx.get("amount_usd", 0)),
            )
            f_merchant = executor.submit(
                self.analyzer.merchant_risk_profile, tx.get("merchant", ""),
            )
            f_client = executor.submit(
                self.analyzer.client_flags, tx.get("client_id", ""),
            )

        logs = f_logs.result(timeout=60)
        policies, similar_cases = f_rag.result(timeout=60)
        merchant_risk = f_merchant.result(timeout=60)
        client_history = f_client.result(timeout=60)

        # Steps 7+8 — resolve + judge
        resolution = self.resolution_svc.resolve(
            tx_data=tx,
            policies=policies,
            similar_cases=similar_cases,
            logs=logs,
            merchant_risk=merchant_risk,
            client_history=client_history,
            motivo=req.motivo,
            cliente_vip=req.cliente_vip,
        )
        judge = self.resolution_svc.judge(
            resolution=resolution,
            full_context={
                "transaction": tx,
                "motivo": req.motivo,
                "policies": policies,
                "similar_cases": similar_cases,
                "merchant_risk": merchant_risk,
                "client_history": client_history,
                "logs": logs,
            },
        )

        # Step 9 — html report
        report_data = {
            "transaction": tx,
            "resolution": resolution,
            "judge_evaluation": judge,
            "agent_analysis": (
                f"Pipeline directo — {txn_id}: {tx.get('merchant', '')}, "
                f"USD {tx.get('amount_usd', '')}, canal {tx.get('channel', '')}, "
                f"país {tx.get('country', '')}, score fraude {tx.get('fraud_score', '')}."
            ),
            "merchant_risk": merchant_risk,
            "client_profile": client_history,
            "logs": logs,
            "policies_evaluated": resolution.get("policy_verdicts", []),
            "similar_cases": similar_cases,
            "hitl_decision": None,
            "cache_hit": False,
            "guardrail_warnings": resolution.get("guardrail_warnings", []),
        }
        html = self.report_gen.render(report_data)

        # Aggregate LLM usage
        usage = self._aggregate_usage(resolution, judge, model_name)
        return html, usage

    def run_streaming(
        self, req: AnalyzeRequest, model_name: str = "",
    ) -> Generator[StreamEvent, None, None]:
        """Execute the pipeline yielding (step, data) events for SSE streaming."""
        txn_id = req.transaction_id
        yield ("start", {"transaction_id": txn_id})

        # Cache check
        cache_key = f"{txn_id}|{req.cliente_vip}"
        cached_html = self.db.get_cached_report(cache_key)
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
        payment_method = tx.get("payment_method", "")
        country = tx.get("country", "")
        fraud_score = int(tx.get("fraud_score", 0))

        logs = []
        policies = []
        similar_cases = []
        merchant_risk = {}
        client_history = {}

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self.db.get_logs_for_transaction, txn_id): "logs",
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
            for future in as_completed(futures, timeout=60):
                name = futures[future]
                result = future.result()
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

        # Resolve (LLM)
        yield ("resolving", {})
        resolution = self.resolution_svc.resolve(
            tx_data=tx,
            policies=policies,
            similar_cases=similar_cases,
            logs=logs,
            merchant_risk=merchant_risk,
            client_history=client_history,
            motivo=req.motivo,
            cliente_vip=req.cliente_vip,
        )
        verdicts = resolution.get("policy_verdicts", [])
        yield ("resolved", {
            "action": resolution.get("recommended_action", ""),
            "risk_level": resolution.get("risk_level", ""),
            "verdicts": len(verdicts),
            "blockers": sum(1 for v in verdicts if v.get("verdict") == "BLOCKER"),
            "fails": sum(1 for v in verdicts if v.get("verdict") == "FAIL"),
            "warnings": len(resolution.get("guardrail_warnings", [])),
        })

        # Judge (LLM)
        yield ("judging", {})
        judge = self.resolution_svc.judge(
            resolution=resolution,
            full_context={
                "transaction": tx,
                "motivo": req.motivo,
                "policies": policies,
                "similar_cases": similar_cases,
                "merchant_risk": merchant_risk,
                "client_history": client_history,
                "logs": logs,
            },
        )
        yield ("judged", {
            "score": judge.get("overall_score", 0),
            "approved": judge.get("approved", False),
        })

        # Render HTML report
        report_data = {
            "transaction": tx,
            "resolution": resolution,
            "judge_evaluation": judge,
            "agent_analysis": (
                f"Pipeline directo — {txn_id}: {tx.get('merchant', '')}, "
                f"USD {tx.get('amount_usd', '')}, canal {tx.get('channel', '')}, "
                f"país {tx.get('country', '')}, score fraude {tx.get('fraud_score', '')}."
            ),
            "merchant_risk": merchant_risk,
            "client_profile": client_history,
            "logs": logs,
            "policies_evaluated": resolution.get("policy_verdicts", []),
            "similar_cases": similar_cases,
            "hitl_decision": None,
            "cache_hit": False,
            "guardrail_warnings": resolution.get("guardrail_warnings", []),
        }
        html = self.report_gen.render(report_data)
        usage = self._aggregate_usage(resolution, judge, model_name)
        yield ("done", {"html": html, "usage": usage})

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
