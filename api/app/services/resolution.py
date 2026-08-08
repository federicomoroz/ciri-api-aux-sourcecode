"""
Service layer for resolution and judge operations.

Extracts orchestration logic from routes/analyze.py, keeping HTTP handlers thin.
LLM failures are re-raised — callers must handle errors explicitly.
"""

import json
import logging

from ..analysis.analyzer import Analyzer
from ..domain import decision
from ..domain.constants import (
    ALERT_EVENT_BLOCKER_REJECT,
    ALERT_EVENT_HITL_REQUIRED,
    ALERT_SOURCE_RESOLVE,
    FALLBACK_TX_ID,
    GUARDRAIL_AUTO_CORRECTED_PREFIX,
    GUARDRAIL_HITL_REASON_GENERIC,
    GUARDRAIL_MAX_COMPENSATION_RATIO,
    GUARDRAIL_MAX_CONFIDENCE,
    GUARDRAIL_MIN_FAILS_FOR_WARNING,
    JUDGE_APPROVAL_THRESHOLD,
    LLM_MAX_CRITICAL_LOGS,
    PASO_JUEZ,
    PASO_POLITICAS,
    PASO_RESOLUCION,
    TRACE_JUDGE,
    TRACE_RESOLVE,
)
from ..domain.context import CaseContext
from ..domain.enums import ResolutionOutcome, RiskLevel, Severity, VerdictType
from ..domain.models import JudgeEvaluationOutput, PolicyVerdictOutput, ResolutionOutput
from ..llm import prompts
from ..llm.client import LLMClient, LLMResult
from ..llm.parsing import validate_llm_output
from ..observability.tracer import Tracer
from ..rag.formatter import annotate_by_motivo, format_cases_for_prompt, format_policies_for_prompt

logger = logging.getLogger(__name__)

# Los unicos veredictos que significan algo. Se deriva del enum para que agregar
# uno nuevo no deje esta lista atras.
_VERDICTOS_VALIDOS = frozenset(v.value for v in VerdictType)
# Los mismos, para el mensaje que lee una persona. Enumerarlos a mano hacia
# que agregar un veredicto dejara el mensaje mintiendo.
_VEREDICTOS_LEGIBLES = "/".join(sorted(_VERDICTOS_VALIDOS))


class ResolutionService:
    def __init__(
        self,
        llm: LLMClient | None = None,
        tracer: Tracer | None = None,
        llm_resolution: LLMClient | None = None,
        llm_judge: LLMClient | None = None,
        modelos=None,
        demo: bool = False,
        api_key: str = "",
        alertas=None,
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
        self.tracer = tracer
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
        precedent_summary = self._build_precedent_summary(
            ctx.similar_cases, ctx.motivo,
            tx_merchant=ctx.transaction.get("merchant", ""),
        )
        outcome["precedent_summary"] = precedent_summary
        outcome.update(decision.compensacion_por_sla(ctx.sla, ctx.transaction))

        resolution, synth_result = self._synthesize_resolution(
            ctx, policy_verdicts, self._summarize_logs(ctx.logs), trace_id, outcome,
        )

        # Detectar la alucinacion ANTES de corregirla: una vez aplicado el
        # override determinista, lo que propuso el modelo ya no es observable.
        warnings = self._detect_divergence(resolution, outcome, policy_verdicts)
        if resolution.get("confidence") == 0.0 and not resolution.get("next_steps"):
            warnings.append(
                "GUARDRAIL: el modelo no devolvio una resolucion utilizable — el informe "
                "sale con la decision deterministica y sin analisis"
            )
        propuesta = self._extraer_propuesta(resolution)

        # Override LLM decisions with deterministic values (always).
        resolution["policy_verdicts"] = policy_verdicts
        resolution["recommended_action"] = outcome["recommended_action"]
        resolution["risk_level"] = outcome["risk_level"]
        resolution["requires_hitl"] = outcome["requires_hitl"]
        resolution["precedent_summary"] = precedent_summary
        if outcome["hitl_reason"]:
            resolution["hitl_reason"] = outcome["hitl_reason"]
        if "compensation_applicable" in outcome:
            resolution["compensation_applicable"] = outcome["compensation_applicable"]
            resolution["compensation_amount_usd"] = outcome["compensation_amount_usd"]

        warnings += self._validate_resolution(resolution, ctx.transaction)
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
        )
        result = self._completar(PASO_POLITICAS, sys_eval, usr_eval, trace_id)
        verdicts = validate_llm_output(result.text, PolicyVerdictOutput, [])
        verdicts = decision.degradar_blockers_no_habilitados(verdicts, ctx.policies)
        return verdicts, result

    @staticmethod
    def _extraer_propuesta(resolution: dict) -> dict:
        """Los campos que el override esta por sobrescribir, tal como los propuso.

        Se toma antes de la correccion. Despues no existe: la resolucion
        entregada tiene la decision del codigo, y calificar eso es calificar al
        codigo.
        """
        return {
            campo: resolution.get(campo)
            for campo in (
                "recommended_action", "risk_level", "requires_hitl",
                "compensation_applicable", "confidence",
            )
        }



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

    # Como se lee una resolucion previa. Fuente unica: las mismas palabras deciden
    # la implicacion que se le cuenta al modelo y el conteo de la tendencia. Estaban
    # escritas en tres lugares del mismo metodo. Se evalua en orden.
    _CLASES_DE_RESOLUCION: tuple[tuple[str, tuple[str, ...], str], ...] = (
        ("sin_resolver", ("sin resolucion", "pendiente"),
         "caso similar permanece sin resolver — sugiere que este tipo de caso requiere "
         "investigacion adicional antes de decidir"),
        ("cerrado", ("cerrado",),
         "caso similar fue cerrado sin resolucion explicita — riesgo de que el caso actual "
         "siga el mismo camino si no se investiga la causa raiz antes de decidir"),
        ("aprobado", ("aprobado", "a favor"),
         "precedente fue aprobado — patron favorable al cliente para este tipo de caso"),
        ("rechazado", ("rechazado", "denegado"),
         "precedente fue rechazado — patron desfavorable al cliente para este tipo de caso"),
        ("parcial", ("parcial",),
         "precedente resuelto con reembolso parcial — solucion intermedia para este tipo de caso"),
    )

    @staticmethod
    def _clasificar_resolucion(resolution: str) -> tuple[str | None, str | None]:
        """(clase, implicacion) de como se resolvio un precedente."""
        texto = (resolution or "").lower()
        for clase, claves, implicacion in ResolutionService._CLASES_DE_RESOLUCION:
            if any(k in texto for k in claves):
                return clase, implicacion
        return None, None

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

        _, implicacion = ResolutionService._clasificar_resolucion(resolution)
        return f"{linea}. Nota: {implicacion}" if implicacion else linea

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

        # Tendencia sobre TODOS los precedentes, con el mismo criterio que las notas.
        clases = [
            ResolutionService._clasificar_resolucion(c.get("resolution", ""))[0]
            for c, _, _ in annotated
        ]
        aprobados = clases.count("aprobado")
        rechazados = clases.count("rechazado")
        total = len(annotated)

        if aprobados > rechazados:
            tendencia = "tendencia favorable al cliente"
        elif rechazados > aprobados:
            tendencia = "tendencia desfavorable al cliente"
        else:
            tendencia = "sin tendencia clara"

        patron = (
            f"Patron: de {total} precedentes, {aprobados} aprobados, "
            f"{rechazados} rechazados — {tendencia}"
        )

        con_motivo = [c for c, label, _ in annotated if label is not None]
        if con_motivo:
            aprobados_motivo = sum(
                1 for c in con_motivo
                if ResolutionService._clasificar_resolucion(c.get("resolution", ""))[0] == "aprobado"
            )
            patron += (
                f". Motivo similar: {len(con_motivo)}/{total}, {aprobados_motivo} aprobados"
            )
        parts.append(patron)

        return " | ".join(parts)






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
        if "compensation_applicable" in outcome:
            propuesta_comp = bool(llm_proposal.get("compensation_applicable", False))
            if propuesta_comp != outcome["compensation_applicable"]:
                warnings.append(
                    f"GUARDRAIL: el modelo propuso compensation_applicable={propuesta_comp} "
                    f"contra un SLA que dice {outcome['compensation_applicable']} — "
                    f"corregido segun POL-SLA-004"
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
                f"GUARDRAIL: Compensacion USD {comp:.2f} excede el monto original USD "
                f"{tx_amount:.2f} en >{(GUARDRAIL_MAX_COMPENSATION_RATIO - 1) * 100:.0f}%"
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
