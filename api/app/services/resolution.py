"""
Service layer for resolution and judge operations.

Extracts orchestration logic from routes/analyze.py, keeping HTTP handlers thin.
LLM failures are re-raised — callers must handle errors explicitly.
"""

import json
import logging

from ..analysis.analyzer import Analyzer
from ..domain.constants import (
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
    POLICY_SEED_BLOQUEANTES,
    RISK_FRAUD_SEVERE,
    RISK_HIGH_MIN_FAILS,
    SLA_COMPENSATION_MAX_USD,
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


class ResolutionService:
    def __init__(
        self,
        llm: LLMClient,
        tracer: Tracer,
        llm_resolution: LLMClient | None = None,
        llm_judge: LLMClient | None = None,
    ):
        # Un cliente por paso. `llm_judge` cae en el de sintesis si no se
        # especifica, que era el comportamiento anterior: evaluar y resolver
        # piden un razonamiento parecido, asi que compartir modelo es un default
        # razonable — pero ahora es un default, no una atadura.
        self.llm = llm
        self.llm_resolution = llm_resolution or llm
        self.llm_judge = llm_judge or self.llm_resolution
        self.tracer = tracer

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
        outcome = self._determine_outcome(policy_verdicts, ctx.transaction)
        precedent_summary = self._build_precedent_summary(
            ctx.similar_cases, ctx.motivo,
            tx_merchant=ctx.transaction.get("merchant", ""),
        )
        outcome["precedent_summary"] = precedent_summary
        outcome.update(self._determine_compensation(ctx.sla, ctx.transaction))

        resolution, synth_result = self._synthesize_resolution(
            ctx, policy_verdicts, self._summarize_logs(ctx.logs), trace_id, outcome,
        )

        # Detectar la alucinacion ANTES de corregirla: una vez aplicado el
        # override determinista, lo que propuso el modelo ya no es observable.
        warnings = self._detect_divergence(resolution, outcome, policy_verdicts)
        propuesta = self._propuesta_del_modelo(resolution)

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
        return {
            **resolution,
            "guardrail_warnings": warnings,
            "trace_id": trace_id,
            "_usage": usage,
            # Lo que propuso el modelo antes del override. Viaja para que el
            # Juez califique al modelo y no a la correccion: sin esto,
            # policy_consistency y risk_assessment evaluaban lo que el codigo
            # ya garantizaba y no podian bajar de 10 por construccion.
            "_propuesta_del_modelo": propuesta,
        }

    def _eval_policies(self, ctx: CaseContext, trace_id: str) -> tuple[list[dict], LLMResult]:
        """Step 1: LLM policy evaluation. Raises on failure."""
        sys_eval, usr_eval = prompts.v1_policy_eval.render(
            transaction=ctx.transaction,
            policies_text=format_policies_for_prompt(ctx.policies),
            policy_count=len(ctx.policies),
            merchant_risk=ctx.merchant_risk,
            client_history=ctx.client_history,
        )
        result = self.llm.complete(sys_eval, usr_eval, trace_id=trace_id)
        verdicts = validate_llm_output(result.text, PolicyVerdictOutput, [])
        verdicts = self._sanitize_verdicts(verdicts, ctx.policies)
        return verdicts, result

    @staticmethod
    def _propuesta_del_modelo(resolution: dict) -> dict:
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

    @staticmethod
    def _pueden_bloquear(policies: list[dict]) -> frozenset[str]:
        """Que politicas de las recuperadas tienen permitido bloquear.

        Sale de la politica misma (`puede_bloquear`, columna de SQLite que viaja
        en la carga de Qdrant), no de una lista en el codigo: agregar una
        politica bloqueante es un POST, no un deploy.

        Si el documento indexado no trae el campo —un indice armado antes de que
        la columna existiera—, se cae a la semilla del dataset. Reindexar la
        politica, o editarla por la API, la trae al dia.
        """
        codigos = {
            p.get("code") for p in policies
            if p.get("puede_bloquear") in (1, True, "1", "true")
        }
        sin_dato = {
            p.get("code") for p in policies if "puede_bloquear" not in p
        } & POLICY_SEED_BLOQUEANTES
        return frozenset(codigos | sin_dato) - {None}

    @classmethod
    def _sanitize_verdicts(cls, verdicts: list[dict], policies: list[dict]) -> list[dict]:
        """Downgrade invalid BLOCKER verdicts to FAIL.

        Solo las politicas marcadas como bloqueantes pueden emitir un BLOCKER
        legitimo. El resto es sobre-escalada del modelo (por ejemplo, tratar la
        suspension de un comercio como si fuera irreversible).
        """
        habilitadas = cls._pueden_bloquear(policies)
        for v in verdicts:
            if (
                v.get("verdict") == VerdictType.BLOCKER
                and v.get("policy_code") not in habilitadas
            ):
                logger.warning(
                    "BLOCKER degradado a FAIL en %s: la politica no esta marcada como bloqueante",
                    v.get("policy_code"),
                )
                v["verdict"] = VerdictType.FAIL
                v["requires_human_review"] = True
        return verdicts

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
        _strip_keys = {"guardrail_warnings", "_usage", "trace_id", "_propuesta_del_modelo"}
        judge_resolution = {k: v for k, v in resolution.items() if k not in _strip_keys}
        if str(judge_resolution.get("hitl_reason", "")).startswith(GUARDRAIL_AUTO_CORRECTED_PREFIX):
            judge_resolution["hitl_reason"] = GUARDRAIL_HITL_REASON_GENERIC

        system, user = prompts.v1_judge.render(
            full_context=full_context,
            resolution=judge_resolution,
            propuesta=resolution.get("_propuesta_del_modelo"),
        )
        llm_result = self.llm_judge.complete(system, user, trace_id=trace_id)
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
    def _determine_compensation(sla: dict, tx_data: dict) -> dict:
        """Compensacion a partir del SLA ya calculado. Sin LLM.

        POL-SLA-004: incumplir el plazo habilita compensacion, con tope fijo. Los
        dias habiles ya los conto `Analyzer.check_sla`; aca solo se lee su
        resultado, porque un umbral contra una fecha es exactamente lo que el
        codigo resuelve mejor que un modelo.

        Sin dato de SLA no hay nada que determinar: se devuelve vacio y la
        decision queda en el modelo, sujeta a `_validate_resolution`.
        """
        if not sla or "within_sla" not in sla:
            return {}
        incumplido = not sla.get("within_sla", True)
        monto = float(tx_data.get("amount_usd", 0) or 0)
        return {
            "compensation_applicable": incumplido,
            # Compensar mas que el cargo original no tiene sentido, y el tope de
            # la politica es un maximo, no un monto fijo.
            "compensation_amount_usd": (
                round(min(SLA_COMPENSATION_MAX_USD, monto), 2) if incumplido and monto else 0.0
            ),
        }

    @staticmethod
    def _determine_outcome(policy_verdicts: list[dict], tx_data: dict) -> dict:
        """Accion y riesgo a partir de los veredictos. Sin LLM.

        Reglas:
        - Algun veredicto BLOCKER → REJECT + riesgo BLOCKER
        - Algun FAIL (sin BLOCKER) → PENDING_HITL + riesgo HIGH o MEDIUM
        - Algun requires_human_review → PENDING_HITL (red de seguridad)
        - Todo PASS/WARNING → APPROVE + riesgo LOW o MEDIUM
        - Sin veredictos → PENDING_HITL: no hay evidencia de nada
        """
        if not policy_verdicts:
            # Falla cerrado. "Ninguna politica fallo" y "no se evaluo ninguna
            # politica" no son lo mismo, y sin esta rama daban lo mismo: APPROVE.
            # Se llega aca por dos caminos reales —Qdrant caido, que el nodo
            # `Buscar Politicas` deja pasar con `continueRegularOutput`, o un JSON
            # invalido del modelo, que `validate_llm_output` degrada a lista
            # vacia—, o sea que una falla de infraestructura terminaba aprobando
            # contracargos sola.
            logger.error("Sin veredictos de politica: se deriva a revision humana")
            return {
                "recommended_action": ResolutionOutcome.PENDING_HITL,
                "risk_level": RiskLevel.HIGH,
                "risk_reason": "No se pudo evaluar ninguna politica: sin evidencia no hay decision",
                "requires_hitl": True,
                "hitl_reason": (
                    "No se evaluo ninguna politica — revisar si el vector store respondio "
                    "y si la evaluacion del modelo devolvio un JSON valido"
                ),
            }

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
