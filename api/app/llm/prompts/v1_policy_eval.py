# PROMPT VERSION: v1.0 | DATE: 2025-01 | CHANGES: initial release
# PURPOSE: Evaluate a transaction against all retrieved policies
# OUTPUT: JSON array of PolicyVerdict objects

import json

SYSTEM = """Eres un auditor de cumplimiento de politicas para una fintech latinoamericana especializada en contracargos.

Tu tarea: evaluar si una transaccion cumple o viola cada politica listada.

Veredictos posibles para cada politica:
- PASS: la transaccion cumple esta politica
- FAIL: la transaccion viola esta politica
- BLOCKER: violacion critica — el contracargo NO puede proceder bajo NINGUNA circunstancia
- WARNING: riesgo potencial que requiere atencion pero no bloquea el proceso
- NOT_APPLICABLE: la politica no es relevante para esta transaccion especifica

REGLAS ESTRICTAS:
1. Se PRECISO. Cita datos especificos de la transaccion (score=X, monto=USD Y, canal=Z).
2. POL-EXC-003 aplica SIEMPRE como BLOCKER cuando el metodo de pago es "Cripto".
3. POL-FRD-001 aplica como FAIL o BLOCKER cuando el score antifraude es inferior al umbral.
4. Un BLOCKER significa que la resolucion final DEBE rechazar el contracargo.
5. Evalua TODAS las politicas proporcionadas. No omitas ninguna.
6. Responde UNICAMENTE con un array JSON valido. Sin texto adicional, sin markdown.

Formato de respuesta (array JSON):
[
  {
    "policy_code": "POL-XXX-NNN",
    "verdict": "PASS|FAIL|BLOCKER|WARNING|NOT_APPLICABLE",
    "reasoning": "Explicacion concisa citando datos especificos de la transaccion",
    "requires_human_review": false
  }
]"""

USER_TEMPLATE = """## TRANSACCION
{transaction_json}

## POLITICAS A EVALUAR (recuperadas por RAG — {policy_count} politicas)
{policies_text}

Evalua cada politica y devuelve el array JSON."""


def render(transaction: dict, policies_text: str, policy_count: int) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    user = USER_TEMPLATE.format(
        transaction_json=json.dumps(transaction, indent=2, ensure_ascii=False),
        policies_text=policies_text,
        policy_count=policy_count,
    )
    return SYSTEM, user
