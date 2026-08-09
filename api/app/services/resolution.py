"""
Service layer for resolution and judge operations.

Extracts orchestration logic from routes/analyze.py, keeping HTTP handlers thin.
LLM failures are re-raised — callers must handle errors explicitly.
"""

import json
import logging

from ..domain import decision, precedentes
from ..domain.constants import (
    ALERT_EVENT_BLOCKER_REJECT,
    ALERT_EVENT_HITL_REQUIRED,
    ALERT_SOURCE_RESOLVE,
    FALLBACK_TX_ID,
    GUARDRAIL_AUTO_CORRECTED_PREFIX,
    GUARDRAIL_HITL_REASON_GENERIC,
    JUDGE_APPROVAL_THRESHOLD,
    PASO_JUEZ,
    PASO_POLITICAS,
    PASO_RESOLUCION,
    TRACE_JUDGE,
    TRACE_RESOLVE,
)
from ..domain.context import CaseContext
from ..domain.contratos import Completador, SumideroDeAlertas
from ..domain.enums import RiskLevel, Severity
from ..domain.models import JudgeEvaluationOutput, PolicyVerdictOutput, ResolutionOutput
from ..llm import prompts
from ..llm.client import LLMClient, LLMResult
from ..llm.parsing import validate_llm_output
from ..observability.tracer import NoOpTracer, Tracer
from ..rag.formatter import format_cases_for_prompt, format_policies_for_prompt
from . import guardrails

logger = logging.getLogger(__name__)


class ResolutionService:
    def __init__(
        self,
        llm: LLMClient | None = None,
        tracer: Tracer | None = None,
        llm_resolution: LLMClient | None = None,
        llm_judge: LLMClient | None = None,
        modelos: Completador | None = None,
        demo: bool = False,
        api_key: str = "",
        alertas: SumideroDeAlertas | None = None,
    ):
        """Este servicio no conoce clientes de modelo. Pide por paso.

        Con `modelos` (un `ModelosService`), cada llamada dice **que paso** es y
        recibe la respuesta: que proveedor, que modelo y con que credencial lo
        deciden el servicio de configuracion y el `LLMManager`, que es el unico
        que sabe que existen los clientes.

        No es una preferencia de estilo. Mientras el que llama podia elegir el
        cliente, podia elegir el equivocado — y paso: el juez resolvia el
        servicio del modo demo y despues invocaba al de produccion, asi que la
        mitad del pipeline se iba por Anthropic sin credito. Ahora el unico
        parametro es el nombre del paso, y no hay forma de equivocarlo en
        silencio.

        `demo` y `api_key` viajan con el servicio: son el modo de esta
        ejecucion, no una decision por llamada.

        Los clientes explicitos siguen aceptandose para los tests, que quieren
        guionar respuestas sin levantar la configuracion entera.

        `alertas` es donde se anotan los eventos operativos —un rechazo
        automatico, un caso que necesita una persona—. Es un callable y no la
        base: este servicio no tiene por que saber que existe SQLite. Sin el,
        no se alerta; es lo que quieren los tests.
        """
        self._modelos = modelos
        self._demo = demo
        self._api_key = api_key
        self._llm = llm
        self._llm_resolution = llm_resolution or llm
        self._llm_judge = llm_judge or self._llm_resolution
        # `None` significa «sin observabilidad», no «sin trazador»: el servicio
        # llama a `tracer.trace()` sin preguntar, asi que guardarlo crudo hace
        # que la opcionalidad que declara la firma reviente al usarla.
        self.tracer = tracer or NoOpTracer()
        self._alertas = alertas

    def _completar(self, paso: str, system: str, user: str, trace_id: str | None = None):
        """La respuesta del modelo de ese paso."""
        if self._modelos is not None:
            return self._modelos.completar(
                paso, system, user,
                demo=self._demo, api_key=self._api_key, trace_id=trace_id,
            )
        fijo = {
            PASO_POLITICAS: self._llm,
            PASO_RESOLUCION: self._llm_resolution,
            PASO_JUEZ: self._llm_judge,
        }[paso]
        return fijo.complete(system, user, trace_id=trace_id)

    def resolve(self, ctx: CaseContext) -> dict:
        """Full resolution pipeline: policy eval -> log summary -> resolution synthesis -> guardrails.

        Raises on LLM failure — never produces incomplete resolutions silently.
        """
        tx_id = ctx.transaction_id or FALLBACK_TX_ID
        trace_id = self.tracer.trace(
            TRACE_RESOLVE,
            input={"transaction_id": tx_id, "motivo": ctx.motivo, "cliente_vip": ctx.cliente_vip},
            output={},
            metadata={
                "merchant": ctx.transaction.get("merchant", ""),
                "amount_usd": ctx.transaction.get("amount_usd", 0),
            },
        )

        policy_verdicts, eval_result = self._eval_policies(ctx, trace_id)

        # Deterministic outcome — code decides, LLM explains.
        outcome = decision.decidir(policy_verdicts, ctx.transaction)
        precedent_summary = precedentes.resumir_precedentes(
            ctx.similar_cases, ctx.motivo,
            tx_merchant=ctx.transaction.get("merchant", ""),
        )
        outcome["precedent_summary"] = precedent_summary
        outcome.update(decision.compensacion_por_sla(ctx.sla, ctx.transaction))

        resumen_de_logs = precedentes.resumir_logs(ctx.logs)
        resolution, synth_result = self._synthesize_resolution(
            ctx, policy_verdicts, resumen_de_logs, trace_id, outcome,
        )

        # Detectar la alucinacion ANTES de corregirla: una vez aplicado el
        # override determinista, lo que propuso el modelo ya no es observable.
        warnings = guardrails.antes_del_override(resolution, outcome, policy_verdicts)
        if resolution.get("confidence") == 0.0 and not resolution.get("next_steps"):
            warnings.append(
                "GUARDRAIL: el modelo no devolvio una resolucion utilizable — el informe "
                "sale con la decision deterministica y sin analisis"
            )
        propuesta = guardrails.propuesta_del_modelo(resolution)

        # Override LLM decisions with deterministic values (always).
        resolution["policy_verdicts"] = policy_verdicts
        resolution["recommended_action"] = outcome["recommended_action"]
        resolution["risk_level"] = outcome["risk_level"]
        resolution["requires_hitl"] = outcome["requires_hitl"]
        resolution["precedent_summary"] = precedent_summary
        # El resumen de logs es un conteo de severidades mas los patrones que
        # detecta `patrones.detect_error_patterns`: lo calcula el codigo y se lo
        # pasa al modelo como contexto. Sin esta linea el modelo lo parafraseaba
        # y su version era la que quedaba, asi que el informe podia mostrar un
        # resumen que no coincidia con los logs de al lado.
        resolution["log_summary"] = resumen_de_logs
        if outcome["hitl_reason"]:
            resolution["hitl_reason"] = outcome["hitl_reason"]
        if "compensation_applicable" in outcome:
            resolution["compensation_applicable"] = outcome["compensation_applicable"]
            resolution["compensation_amount_usd"] = outcome["compensation_amount_usd"]

        # `resolution` ya trae el override aplicado: la revision de este momento
        # mira los campos que el codigo NO fija.
        warnings += guardrails.despues_del_override(resolution, ctx.transaction)
        usage = {
            "input_tokens": eval_result.input_tokens + synth_result.input_tokens,
            "output_tokens": eval_result.output_tokens + synth_result.output_tokens,
            "call_count": 2,
        }
        resultado = {
            **resolution,
            "guardrail_warnings": warnings,
            "trace_id": trace_id,
            "_usage": usage,
            # Lo que propuso el modelo antes del override. Viaja para que el
            # Juez califique al modelo y no a la correccion: sin esto,
            # policy_consistency y risk_assessment evaluaban lo que el codigo
            # ya garantizaba y no podian bajar de 10 por construccion.
            "propuesta_del_modelo": propuesta,
        }
        self._alertar(resultado, ctx.transaction.get("id", ""))
        return resultado

    def _alertar(self, resultado: dict, tx_id: str) -> None:
        """Anota los eventos operativos que valen la pena mirar despues.

        Vive aca y no en la ruta porque **este es el unico lugar por el que
        pasan los cuatro caminos**: el webhook de n8n, el pipeline directo, el
        panel y la llamada suelta a la API. Estaba en `routes/analyze.py`, asi
        que un caso resuelto por el pipeline directo derivaba a una persona sin
        dejar alerta, y el mismo caso por n8n si la dejaba. En una fintech, que
        un HITL aparezca o no en el log segun por donde entro el caso es un
        problema de auditoria.

        Es best-effort a proposito: no poder anotar una alerta no puede tumbar
        una resolucion que ya se calculo.
        """
        if self._alertas is None:
            return
        riesgo = resultado.get("risk_level", "")
        if riesgo == RiskLevel.BLOCKER:
            evento, severidad, mensaje = (
                ALERT_EVENT_BLOCKER_REJECT, Severity.ERROR, f"BLOCKER auto-reject: {tx_id}",
            )
        elif resultado.get("requires_hitl"):
            evento, severidad, mensaje = (
                ALERT_EVENT_HITL_REQUIRED, Severity.WARN, f"HITL requerido: {tx_id}",
            )
        else:
            return
        try:
            self._alertas({
                "event_type": evento,
                "severity": severidad,
                "message": mensaje,
                "source": ALERT_SOURCE_RESOLVE,
                "transaction_id": tx_id,
                "metadata": {"risk_level": riesgo},
            })
        except Exception:
            logger.warning("No se pudo anotar la alerta de %s", tx_id, exc_info=True)

    def _eval_policies(self, ctx: CaseContext, trace_id: str) -> tuple[list[dict], LLMResult]:
        """Step 1: LLM policy evaluation. Raises on failure."""
        sys_eval, usr_eval = prompts.v1_policy_eval.render(
            transaction=ctx.transaction,
            policies_text=format_policies_for_prompt(ctx.policies),
            policy_count=len(ctx.policies),
            merchant_risk=ctx.merchant_risk,
            client_history=ctx.client_history,
            # El plazo ya medido. Sin esto el evaluador pedia fechas que el
            # sistema tenia, y las politicas de plazo caian en WARNING.
            sla=ctx.sla,
        )
        result = self._completar(PASO_POLITICAS, sys_eval, usr_eval, trace_id)
        verdicts = validate_llm_output(result.text, PolicyVerdictOutput, [])
        verdicts = decision.degradar_blockers_no_habilitados(verdicts, ctx.policies)
        return verdicts, result




    def _synthesize_resolution(
        self,
        ctx: CaseContext,
        policy_verdicts: list[dict],
        log_summary: str,
        trace_id: str,
        determined_outcome: dict | None = None,
    ) -> tuple[dict, LLMResult]:
        """Step 4: LLM resolution synthesis. Raises on failure."""
        sys_res, usr_res = prompts.v1_resolution.render(
            transaction=ctx.transaction,
            policy_verdicts=json.dumps(policy_verdicts, ensure_ascii=False, indent=2),
            similar_cases=format_cases_for_prompt(ctx.similar_cases, current_motivo=ctx.motivo),
            log_summary=log_summary,
            merchant_risk=ctx.merchant_risk,
            client_history=ctx.client_history,
            motivo=ctx.motivo,
            cliente_vip=ctx.cliente_vip,
            precedent_count=len(ctx.similar_cases),
            log_count=len(ctx.logs),
            determined_outcome=determined_outcome,
            sla=ctx.sla,
        )
        result = self._completar(PASO_RESOLUCION, sys_res, usr_res, trace_id)
        resolution = validate_llm_output(result.text, ResolutionOutput, {})
        return decision.con_piso(resolution, determined_outcome or {}), result


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
        _strip_keys = {"guardrail_warnings", "_usage", "trace_id", "propuesta_del_modelo"}
        judge_resolution = {k: v for k, v in resolution.items() if k not in _strip_keys}
        if str(judge_resolution.get("hitl_reason", "")).startswith(GUARDRAIL_AUTO_CORRECTED_PREFIX):
            judge_resolution["hitl_reason"] = GUARDRAIL_HITL_REASON_GENERIC

        system, user = prompts.v1_judge.render(
            full_context=full_context,
            resolution=judge_resolution,
            propuesta=resolution.get("propuesta_del_modelo"),
        )
        llm_result = self._completar(PASO_JUEZ, system, user, trace_id)
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












