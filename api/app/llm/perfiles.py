"""Un perfil por familia de modelo: lo que hay que hacer distinto con cada una.

El sistema esta calibrado para Claude. Los prompts, el presupuesto de tokens y
lo que se espera de una respuesta salieron de trabajar con Haiku y Sonnet. Eso
no es un problema —es la configuracion documentada— pero significa que **el
resto de los modelos necesitan traduccion**, y esa traduccion tiene que vivir en
un solo lugar y no repartida en parches.

Lo que cambia entre familias, medido y no supuesto:

- **Presupuesto de tokens.** Los modelos que razonan descuentan lo que piensan
  del mismo `max_tokens` y no lo reportan en `completion_tokens`. Con los 4096
  de Claude, Gemini gastaba ~3.900 pensando y cortaba la respuesta a mitad de
  frase (`finish_reason: length`). Claude no hace eso: subirle el techo seria
  reservar espacio que no usa.

- **Que errores vale la pena reintentar.** Un free tier devuelve 503 cuando el
  proveedor esta cargado, y eso pasa: la primera llamada entra y la siguiente
  rebota. El SDK de Anthropic ya reintenta por su cuenta; el cliente HTTP no,
  porque `httpx` solo reintenta fallos de conexion y un 503 es una respuesta
  valida.

Lo que **no** vive aca es como viene envuelta la respuesta —bloques de codigo,
prosa alrededor, cadenas de razonamiento, comas colgando—. Eso lo resuelve
`parsing.py` para todos por igual, porque son formas que aparecen en cualquier
familia y no vale la pena mantener una lista por proveedor de quien hace cual.

Agregar un modelo nuevo que se comporte distinto es agregar una entrada aca.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.constants import (
    LLM_MAX_TOKENS_ABIERTO,
    LLM_MAX_TOKENS_COMPATIBLE,
    LLM_RETRY_BASE_WAIT_S,
    LLM_RETRY_MAX_WAIT_S,
    LLM_RETRY_STATUS,
    LLM_RPM_ABIERTO,
    LLM_RPM_GEMINI_FREE,
    LLM_RPM_SIN_LIMITE,
)

# Claude ya viene calibrado: no se le impone un piso, se usa `llm_max_tokens`.
LLM_SIN_PISO: int = 0


@dataclass(frozen=True, slots=True)
class Perfil:
    """Como hay que tratar a una familia de modelos."""

    nombre: str
    # Piso de `max_tokens`. Se aplica SIEMPRE, que es lo que un piso significa:
    # un modelo que razona necesita espacio para pensar Y para responder, y
    # quedarse corto no corta el razonamiento sino la respuesta.
    #
    # Antes se aplicaba solo cuando el llamador no habia pedido nada, y eso se
    # detectaba comparando contra `LLM_DEFAULT_MAX_TOKENS`. Ese acoplamiento se
    # rompio solo en cuanto `CB_LLM_MAX_TOKENS` empezo a llegar hasta el cliente:
    # bajarlo por configuracion desactivaba en silencio la unica proteccion
    # contra respuestas truncadas. Para Claude el piso es cero, asi que la
    # configuracion documentada no cambia.
    piso_de_tokens: int = LLM_SIN_PISO
    # Si descuenta su razonamiento del presupuesto de salida. Solo documenta el
    # porque del piso, pero se guarda porque es la razon y no el efecto.
    razona: bool = False
    # Codigos que se reintentan. Un 4xx que no sea 429 no mejora por insistir.
    reintenta: frozenset[int] = field(default_factory=lambda: LLM_RETRY_STATUS)
    espera_base_s: float = LLM_RETRY_BASE_WAIT_S
    espera_maxima_s: float = LLM_RETRY_MAX_WAIT_S
    # Pedidos por minuto que tolera esta familia, o `LLM_RPM_SIN_LIMITE` si no
    # se conoce ninguno. Es el piso por defecto y no la ultima palabra: la cuota
    # depende de la cuenta, y `CB_LLM_RPM` la pisa por proveedor.
    pedidos_por_minuto: int = LLM_RPM_SIN_LIMITE
    nota: str = ""


# Anthropic: la configuracion documentada. No razona sobre el presupuesto de
# salida, asi que los 4096 alcanzan y subirlos seria reservar de mas.
CLAUDE = Perfil(
    nombre="claude",
    piso_de_tokens=LLM_SIN_PISO,
    razona=False,
    nota="La calibracion de referencia. El SDK ya reintenta por su cuenta.",
)

# Gemini razona y no lo reporta: medido, ~3.900 tokens de pensamiento en una
# llamada cuyo `completion_tokens` decia 159.
GEMINI = Perfil(
    nombre="gemini",
    piso_de_tokens=LLM_MAX_TOKENS_COMPATIBLE,
    razona=True,
    pedidos_por_minuto=LLM_RPM_GEMINI_FREE,
    nota="Razona sobre el presupuesto de salida. Con 4096 corta por `length`.",
)

# Los reasoners de otras casas se comportan igual.
RAZONADOR = Perfil(
    nombre="razonador",
    piso_de_tokens=LLM_MAX_TOKENS_COMPATIBLE,
    razona=True,
    nota="Modelo de razonamiento: necesita espacio para pensar y para responder.",
)

# Llama, Qwen y compania no razonan sobre el presupuesto, pero son mas verbosos
# que Claude con estructuras largas: un piso moderado evita truncar los 17
# veredictos sin reservar de mas.
ABIERTO = Perfil(
    nombre="abierto",
    piso_de_tokens=LLM_MAX_TOKENS_ABIERTO,
    razona=False,
    pedidos_por_minuto=LLM_RPM_ABIERTO,
    nota="No razona, pero es mas verboso que Claude en respuestas estructuradas.",
)


# Subcadenas del nombre del modelo que mandan sobre el proveedor: dentro de una
# misma casa conviven modelos que razonan y modelos que no.
POR_MODELO: tuple[tuple[str, Perfil], ...] = (
    ("thinking", RAZONADOR),
    ("reasoner", RAZONADOR),   # deepseek-reasoner
    ("-r1", RAZONADOR),        # deepseek-r1 y derivados
    ("o1-", RAZONADOR),
    ("o3-", RAZONADOR),
    ("o4-", RAZONADOR),
    ("gemini", GEMINI),        # via OpenRouter u otro agregador
    ("claude", CLAUDE),
)


def perfil_de(proveedor: str, modelo: str = "") -> Perfil:
    """El perfil de esa combinacion.

    El modelo manda sobre el proveedor: `openrouter` sirve tanto Llama como
    Gemini, y no se les habla igual. Si no se reconoce ninguno se usa el de
    modelos abiertos, que es el mas tolerante — equivocarse hacia un techo mas
    alto cuesta latencia; equivocarse hacia uno mas bajo corta la respuesta.
    """
    nombre = (modelo or "").lower()
    for marca, perfil in POR_MODELO:
        if marca in nombre:
            return perfil
    # Import perezoso: `proveedores` importa los perfiles de este modulo,
    # asi que pedirlo arriba seria un ciclo. La tabla vive alla porque agregar
    # un proveedor tiene que ser una sola edicion.
    from .proveedores import PERFIL_POR_PROVEEDOR

    return PERFIL_POR_PROVEEDOR.get((proveedor or "").lower().strip(), ABIERTO)
