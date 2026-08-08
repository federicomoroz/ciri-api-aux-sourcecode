"""Las formas de fallar que el sistema sabe nombrar, y qué se dice de cada una.

Un fallo se lanza desde donde ocurre —eso está bien y es lo que corresponde en
Python: el `raise` desenrolla la pila y nadie tiene que hilar códigos de error por
los retornos—. Lo que **no** puede estar repartido es la traducción a algo que
quien lee entienda, y eso es lo que vive acá.

Antes estaba en tres lugares y de tres maneras:

- `main.py` clasificaba con `MARKER in str(exc).lower()` y escribía su texto,
- `routes/panel.py` volvía a clasificar con los mismos marcadores y escribía otro,
- la página HTML de error escribía un tercero.

O sea: **la misma causa decía tres cosas distintas según por dónde saliera**, y
las tres clasificaban por substring sobre el mensaje completo de la excepción. Esa
fragilidad no es teórica — el workflow de n8n tenía un `.includes('404')` que un
503 disparaba, porque el mensaje traía adentro una página HTML de 263 KB con
«404» en el nombre de una fuente.

**Por qué no alcanza con los `@app.exception_handler` de FastAPI.** Cubren el
borde HTTP, pero no el streaming: cuando el SSE ya empezó a emitir, la respuesta
salió con 200 y ningún handler puede cambiarla. Por eso el panel reclasifica a
mano — no por duplicación, sino porque ahí los handlers no llegan. Con la
clasificación acá, los dos caminos dicen lo mismo.

Cómo se clasifica, en orden de confianza:

1. **Por tipo de excepción** — es lo único que no depende de cómo redactó el
   proveedor su mensaje.
2. **Por código de estado**, cuando el SDK lo expone.
3. **Por substring**, último y solo donde el SDK no da otra cosa. Anclado a
   marcadores que el proveedor documenta, no a números sueltos.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .constants import LLM_BAD_KEY_MARKER, LLM_CREDIT_EXHAUSTED_MARKER


class Fallo(StrEnum):
    """Las causas que el sistema distingue. El resto es `None`: un 500 honesto."""

    SIN_SALDO = "sin_saldo"
    CLAVE_INVALIDA = "clave_invalida"
    CUOTA_DE_EMBEDDINGS = "cuota_de_embeddings"
    PROVEEDOR_DESCONOCIDO = "proveedor_desconocido"
    DATO_INEXISTENTE = "dato_inexistente"


@dataclass(frozen=True, slots=True)
class Respuesta:
    """Qué se dice de un fallo, una sola vez, para todos los caminos.

    `detalle` se escribe para quien está probando el sistema, no para quien lo
    programó: dice qué pasó y qué puede hacer al respecto. Si no hay nada que
    pueda hacer, lo dice también — es mejor que sugerir una acción inútil.
    """

    status: int
    titulo: str
    detalle: str
    # Si vale la pena dejar constancia operativa. Quedarse sin saldo la vale;
    # que alguien pegue mal su clave, no: es su clave y ya se lo dijimos.
    alerta: bool = False

    @property
    def para_leer(self) -> str:
        """Titular y explicacion juntos, para donde solo entra un texto.

        Es el caso del streaming del panel: el evento de error lleva un solo
        campo, y sin el titular quedaba la explicacion sin decir que paso.
        """
        return f"{self.titulo}. {self.detalle}"


RESPUESTAS: dict[Fallo, Respuesta] = {
    Fallo.SIN_SALDO: Respuesta(
        status=402,
        titulo="El proveedor del modelo se quedó sin saldo",
        detalle=(
            "La clave configurada no tiene crédito, así que el análisis no puede correr. "
            "Se puede seguir probando con una clave propia: en el panel, campo «API key», "
            "o mandando api_key en el cuerpo de la petición. El resto del sistema "
            "—consultas, RAG, informes— no depende de esto."
        ),
        alerta=True,
    ),
    Fallo.CLAVE_INVALIDA: Respuesta(
        status=401,
        titulo="El proveedor rechazó esa API key",
        detalle=(
            "Revisá que esté completa y con el prefijo que corresponde al proveedor "
            "(las de Anthropic empiezan con «sk-ant-»). Con el modo demo encendido, los "
            "casos de ejemplo se ven igual sin necesidad de clave."
        ),
    ),
    Fallo.CUOTA_DE_EMBEDDINGS: Respuesta(
        status=429,
        titulo="El proveedor de embeddings cortó la consulta",
        detalle=(
            "Voyage AI rechazó la petición por límite de frecuencia. Las búsquedas "
            "semánticas —políticas y casos similares— quedan sin servicio hasta que se "
            "libere la cuota. Reintentar en un minuto."
        ),
    ),
    Fallo.PROVEEDOR_DESCONOCIDO: Respuesta(
        status=400,
        titulo="Ese proveedor de modelo no está registrado",
        detalle=(
            "Los que el sistema sabe construir están en «llm/proveedores.py». Para uno "
            "nuevo hay que agregarlo ahí, o pasar su URL en CB_LLM_BASE_URL."
        ),
    ),
    Fallo.DATO_INEXISTENTE: Respuesta(
        status=404,
        titulo="No existe en la base",
        detalle="Revisar el identificador. El listado completo está en GET /api/transactions.",
    ),
}


def _codigo(exc: Exception) -> int | None:
    """El código de estado que traiga la excepción, venga como venga."""
    for atributo in ("status_code", "code", "http_status"):
        valor = getattr(exc, atributo, None)
        if isinstance(valor, int):
            return valor
    respuesta = getattr(exc, "response", None)
    codigo = getattr(respuesta, "status_code", None)
    return codigo if isinstance(codigo, int) else None


def clasificar(exc: Exception) -> Fallo | None:
    """De qué falló esto, si el sistema lo sabe nombrar. `None` si no.

    Devolver `None` es una respuesta legítima y frecuente: significa «esto es un
    error de verdad, no una condición prevista», y el llamador responde un 500 con
    su `request_id`. Inventar una causa sería peor que no dar ninguna.
    """
    from ..rag.embedder import EmbeddingRateLimit

    # 1. Por tipo. Lo unico que no depende de como redacto el proveedor.
    if isinstance(exc, EmbeddingRateLimit):
        return Fallo.CUOTA_DE_EMBEDDINGS
    if isinstance(exc, ValueError) and "proveedor desconocido" in str(exc):
        return Fallo.PROVEEDOR_DESCONOCIDO

    texto = str(exc).lower()

    # 2. Por codigo de estado, cuando el SDK lo expone.
    codigo = _codigo(exc)
    if codigo == 401:
        return Fallo.CLAVE_INVALIDA
    if codigo == 404:
        return Fallo.DATO_INEXISTENTE

    # 3. Por substring, ultimo recurso: son marcadores que Anthropic documenta y
    #    que su SDK no expone de otra forma. `credit balance is too low` viaja con
    #    un 400, indistinguible por codigo de cualquier otro pedido mal formado.
    if LLM_CREDIT_EXHAUSTED_MARKER in texto:
        return Fallo.SIN_SALDO
    if LLM_BAD_KEY_MARKER in texto:
        return Fallo.CLAVE_INVALIDA
    return None


def respuesta_de(exc: Exception) -> Respuesta | None:
    """Atajo para quien solo quiere el texto: clasifica y traduce."""
    fallo = clasificar(exc)
    return RESPUESTAS[fallo] if fallo else None
