"""Los numeros que el panel muestra sobre las trazas, calculados una sola vez.

Hay dos fuentes posibles —Langfuse o el registro local— y el panel muestra una u
otra segun cual este activa. Mientras cada una calculaba su propio resumen, los
dos caminos ya habian divergido en cosas que se ven: el costo se redondeaba a 4
decimales en un lado y a 6 en el otro (con modelos gratuitos o baratos, 4
decimales muestran 0.0), y la latencia promedio devolvia `None` sin datos en uno
y un numero en el otro.

Son los mismos numeros con el mismo nombre. Se calculan aca.
"""

from ..domain.constants import COSTO_DECIMALES, LATENCIA_DECIMALES, SCORE_DECIMALES


def resumir_trazas(filas: list[dict]) -> dict:
    """El resumen de un conjunto de trazas ya normalizadas.

    Cada fila necesita `tokens_in`, `tokens_out`, `cost_usd`, `latency_s` y
    `score` (que puede ser None). Normalizar es tarea de quien lee la fuente:
    Langfuse y el registro local guardan formas distintas del mismo hecho.
    """
    entrada = sum(f["tokens_in"] for f in filas)
    salida = sum(f["tokens_out"] for f in filas)
    puntajes = [f["score"] for f in filas if f["score"] is not None]
    # Solo las latencias medidas. Un cero no es «rapido», es «no se registro», y
    # promediarlo como cero hunde el numero.
    latencias = [f["latency_s"] for f in filas if f["latency_s"] > 0]
    return {
        "total_traces": len(filas),
        "total_tokens": entrada + salida,
        "cost_usd": round(sum(f["cost_usd"] for f in filas), COSTO_DECIMALES),
        "avg_judge_score": (
            round(sum(puntajes) / len(puntajes), SCORE_DECIMALES) if puntajes else None
        ),
        "avg_latency_s": (
            round(sum(latencias) / len(latencias), LATENCIA_DECIMALES) if latencias else None
        ),
    }
