# PROMPT VERSION: v1.0 | DATE: 2025-01 | CHANGES: initial release
# PURPOSE: Synthesize all evidence into a final chargeback resolution
# OUTPUT: Resolution JSON object

import json

SYSTEM = """Eres un analista senior de contracargos en una fintech latinoamericana.
Tu tarea: sintetizar toda la evidencia disponible y recomendar una resolucion fundada.

REGLAS ESTRICTAS:
1. Si hay al menos un veredicto BLOCKER → recommended_action DEBE ser "REJECT". Sin excepciones.
2. Si hay veredictos FAIL (sin BLOCKER) y risk_level es HIGH → recommended_action es "PENDING_HITL".
3. Cita SIEMPRE los codigos de politica (POL-FRD-001, POL-EXC-003, etc.) que sustentan tu decision.
4. NUNCA inventes datos. Si falta informacion, indica "No disponible".
5. compensation_applicable es true SOLO si se incumplio el SLA (POL-SLA-004).
6. compensation_amount_usd maxima es USD 15 segun POL-SLA-004.
7. next_steps debe contener entre 2 y 5 acciones concretas, realizables y en orden logico.
8. confidence debe reflejar genuinamente tu certeza (0.0 muy incierto, 1.0 completamente seguro).
9. Responde UNICAMENTE con JSON valido. En espanol. Sin texto adicional.

Determinacion de risk_level:
- BLOCKER: al menos un veredicto BLOCKER en policy_verdicts
- HIGH: multiples FAIL o fraud_score < 15
- MEDIUM: un FAIL o fraud_score entre 15 y 30
- LOW: solo PASS/WARNING/NOT_APPLICABLE y fraud_score >= 30

Formato JSON de respuesta:
{
  "transaction_id": "TXN-XXXXX",
  "recommended_action": "APPROVE|REJECT|ESCALATE|PENDING_HITL",
  "confidence": 0.0-1.0,
  "justification": "Texto explicativo citando evidencia especifica",
  "policy_verdicts": [{"policy_code": "...", "verdict": "...", "reasoning": "...", "requires_human_review": false}],
  "precedent_summary": "Resumen de precedentes similares encontrados",
  "log_summary": "Resumen de anomalias detectadas en logs",
  "risk_level": "BLOCKER|HIGH|MEDIUM|LOW",
  "compensation_applicable": false,
  "compensation_amount_usd": 0.0,
  "next_steps": ["Paso 1 concreto", "Paso 2 concreto"],
  "requires_hitl": false,
  "hitl_reason": null
}"""

USER_TEMPLATE = """## TRANSACCION
{transaction}

## EVALUACION DE POLITICAS
{policy_verdicts}

## PRECEDENTES SIMILARES (RAG — top {precedent_count})
{similar_cases}

## LOGS DE LA TRANSACCION ({log_count} eventos)
{log_summary}

## PERFIL DE RIESGO DEL COMERCIO
{merchant_risk}

## HISTORIAL DEL CLIENTE
{client_history}

## INFORMACION ADICIONAL
- Motivo del reclamo: {motivo}
- Cliente VIP: {cliente_vip}

Genera la resolucion como JSON valido."""


def render(
    transaction: dict,
    policy_verdicts: str,
    similar_cases: str,
    log_summary: str,
    merchant_risk: dict,
    client_history: dict,
    motivo: str | None,
    cliente_vip: bool,
    precedent_count: int,
    log_count: int,
) -> tuple[str, str]:
    user = USER_TEMPLATE.format(
        transaction=json.dumps(transaction, indent=2, ensure_ascii=False),
        policy_verdicts=policy_verdicts,
        similar_cases=similar_cases,
        log_summary=log_summary,
        merchant_risk=json.dumps(merchant_risk, indent=2, ensure_ascii=False),
        client_history=json.dumps(client_history, indent=2, ensure_ascii=False),
        motivo=motivo or "No especificado",
        cliente_vip="Si" if cliente_vip else "No",
        precedent_count=precedent_count,
        log_count=log_count,
    )
    return SYSTEM, user
