"""El contexto de una investigacion, en un solo tipo.

Estaba modelado tres veces con tres juegos de nombres para lo mismo: la
transaccion era `tx_data` en la peticion de n8n, `tx` en el pipeline directo y
`transaction` en el informe; el historial del cliente, `client_history` o
`client_profile`. Cada camino armaba su propia version de la misma cosa.

Los modelos de entrada y salida conservan sus nombres — son contratos que n8n ya
consume — pero por dentro todos convergen aca.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CaseContext:
    """Todo lo que se sabe de un caso antes de pedirle una resolucion al modelo."""

    transaction: dict
    motivo: str | None = None
    cliente_vip: bool = False
    logs: list[dict] = field(default_factory=list)
    policies: list[dict] = field(default_factory=list)
    similar_cases: list[dict] = field(default_factory=list)
    merchant_risk: dict = field(default_factory=dict)
    client_history: dict = field(default_factory=dict)
    # Resultado de Analyzer.check_sla: dias habiles transcurridos contra el
    # limite de la politica. De aca sale la compensacion, que es determinista.
    sla: dict = field(default_factory=dict)

    @property
    def transaction_id(self) -> str:
        return str(self.transaction.get("id", ""))

    def para_el_juez(self) -> dict:
        """La evidencia que ve el Juez, con los nombres que espera su prompt."""
        return {
            "transaction": self.transaction,
            "motivo": self.motivo,
            "policies": self.policies,
            "similar_cases": self.similar_cases,
            "merchant_risk": self.merchant_risk,
            "client_history": self.client_history,
            "logs": self.logs,
        }
