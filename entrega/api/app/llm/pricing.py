"""Costo de una llamada al modelo.

Vivia en dos servicios con la misma formula y ya habian divergido: el pipeline
devolvia 0 cuando el modelo no matcheaba ninguna tarifa y las estadisticas
usaban una tarifa de referencia. El mismo calculo daba dos respuestas segun
quien lo pidiera.
"""

from __future__ import annotations

from ..domain.constants import (
    LLM_PRICING,
    LLM_PRICING_FALLBACK_KEY,
    LLM_PRICING_PER_MTOK,
)


def tarifa_de(model_name: str) -> tuple[float, float]:
    """(precio entrada, precio salida) por millon de tokens.

    Los nombres de modelo traen sufijos de fecha y version, asi que se busca por
    subcadena. Si ninguna tarifa matchea se usa la de referencia: informar costo
    cero seria peor que informar una estimacion.
    """
    modelo = (model_name or "").lower()
    for clave, tarifa in LLM_PRICING.items():
        if clave in modelo:
            return tarifa
    return LLM_PRICING[LLM_PRICING_FALLBACK_KEY]


def estimar_costo_usd(model_name: str, input_tokens: int, output_tokens: int) -> float:
    """Costo en dolares de una llamada, segun la tarifa del modelo."""
    entrada, salida = tarifa_de(model_name)
    return (
        input_tokens / LLM_PRICING_PER_MTOK * entrada
        + output_tokens / LLM_PRICING_PER_MTOK * salida
    )
