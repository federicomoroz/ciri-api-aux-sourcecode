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
    LLM_SIN_TARIFA,
)


def tarifa_de(model_name: str) -> tuple[float, float]:
    """(precio entrada, precio salida) por millon de tokens.

    Los nombres de modelo traen sufijos de fecha y version, asi que se busca por
    subcadena. Si ninguna tarifa matchea se usa la de referencia: informar costo
    cero para un modelo de pago desconocido seria peor que estimar.

    La excepcion son los que este proyecto solo usa por free tier: ahi el cero
    es el dato correcto y la estimacion era el error.
    """
    modelo = (model_name or "").lower()
    # Los que corren por free tier van primero: si no, caian en la tarifa de
    # referencia y una corrida que no costo nada figuraba en diez centavos.
    if any(marca in modelo for marca in LLM_SIN_TARIFA):
        return (0.0, 0.0)
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
