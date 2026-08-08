"""
LLM client abstraction.

Protocol for dependency injection + Anthropic implementation.
All external API calls are wrapped with error handling and logging.
"""

import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import anthropic
import httpx

from ..domain.constants import (
    LLM_DEFAULT_MAX_RETRIES,
    LLM_DEFAULT_MAX_TOKENS,
    LLM_DEFAULT_TEMPERATURE,
    LLM_TIMEOUT_S,
    LLM_TRUNCATION_LENGTH,
    SECONDS_TO_MS,
    TRACE_LLM_CALL,
)
from ..observability.tracer import NoOpTracer, Tracer
from .perfiles import Perfil, perfil_de

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Result from an LLM completion call, including token usage."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = LLM_DEFAULT_MAX_TOKENS,
        temperature: float = LLM_DEFAULT_TEMPERATURE,
        trace_id: str | None = None,
    ) -> LLMResult:
        """Send system + user prompt, return text response with token usage."""
        ...


class AnthropicClient:
    def __init__(self, api_key: str, model: str, tracer: Tracer | None = None, max_retries: int = LLM_DEFAULT_MAX_RETRIES):
        self.client = anthropic.Anthropic(
            api_key=api_key,
            max_retries=max_retries,
            timeout=httpx.Timeout(LLM_TIMEOUT_S, connect=10.0),
        )
        self.model = model
        # Igual que `OpenAICompatibleClient`: `complete()` llama a
        # `tracer.generation()` sin preguntar, asi que el `None` que admite la
        # firma tiene que convertirse en un trazador que no hace nada.
        self.tracer = tracer or NoOpTracer()

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = LLM_DEFAULT_MAX_TOKENS,
        temperature: float = LLM_DEFAULT_TEMPERATURE,
        trace_id: str | None = None,
    ) -> LLMResult:
        start = time.time()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as e:
            logger.error("Anthropic API error: %s", e)
            raise
        except Exception as e:
            logger.error("Unexpected LLM error: %s", e)
            raise

        latency_ms = (time.time() - start) * SECONDS_TO_MS
        text = response.content[0].text

        self.tracer.generation(
            name=TRACE_LLM_CALL,
            model=self.model,
            input=user[:LLM_TRUNCATION_LENGTH],
            output=text[:LLM_TRUNCATION_LENGTH],
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=latency_ms,
            trace_id=trace_id,
        )

        logger.info(
            "LLM call completed: model=%s tokens_in=%d tokens_out=%d latency=%.0fms",
            self.model, response.usage.input_tokens, response.usage.output_tokens, latency_ms,
        )
        return LLMResult(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def close(self) -> None:
        """Cierra el pool de conexiones. Solo hace falta para clientes efimeros."""
        try:
            self.client.close()
        except Exception:
            logger.debug("No se pudo cerrar el cliente Anthropic", exc_info=True)




class OpenAICompatibleClient:
    """`LLMClient` contra cualquier endpoint que hable el protocolo de OpenAI.

    Existe por dos razones, y la segunda importa mas que la primera.

    La primera es practica: Anthropic no tiene free tier, y medir la calidad del
    agente sobre el dataset completo cuesta dinero. Groq y Gemini —entre otros—
    exponen endpoints compatibles con OpenAI y regalan cuota suficiente para
    correr `scripts/evaluar.py` sin pagar nada.

    La segunda es que `docs/architecture.md` afirma que cambiar de proveedor es
    implementar el Protocol y que ningun call site cambia. Mientras hubo una sola
    implementacion, eso era una afirmacion sin demostrar. Esta clase es la prueba:
    no toca un solo llamador.

    **Un score medido con otro proveedor no es el score del sistema entregado.**
    Los prompts estan afinados para Claude y la configuracion documentada es
    Haiku + Sonnet. Lo que una corrida asi mide es que el pipeline es
    independiente del proveedor, y da un punto de comparacion — no reemplaza a la
    medicion sobre la configuracion real.

    Se habla HTTP directo con httpx, que ya es dependencia, en vez de sumar el
    SDK de OpenAI: el contrato que se usa es un POST con cuatro campos.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        tracer: Tracer | None = None,
        max_retries: int = LLM_DEFAULT_MAX_RETRIES,
        perfil: Perfil | None = None,
        proveedor: str = "",
    ):
        if not base_url:
            raise ValueError("OpenAICompatibleClient necesita base_url")
        self.model = model
        self.tracer = tracer or NoOpTracer()
        self.max_retries = max_retries
        # Lo que hay que hacer distinto con esta familia de modelos. El sistema
        # esta calibrado para Claude; el perfil traduce.
        self.perfil = perfil or perfil_de(proveedor, model)
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(LLM_TIMEOUT_S, connect=10.0),
            transport=httpx.HTTPTransport(retries=max_retries),
        )

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = LLM_DEFAULT_MAX_TOKENS,
        temperature: float = LLM_DEFAULT_TEMPERATURE,
        trace_id: str | None = None,
    ) -> LLMResult:
        start = time.time()
        # El piso lo pone el perfil: un modelo que razona descuenta lo que
        # piensa de este mismo presupuesto y con el techo de Claude corta la
        # respuesta a medias. Solo se aplica si el llamador no pidio uno propio.
        if max_tokens == LLM_DEFAULT_MAX_TOKENS:
            max_tokens = max(max_tokens, self.perfil.piso_de_tokens)
        cuerpo = self._pedir({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        })

        latency_ms = (time.time() - start) * SECONDS_TO_MS
        eleccion = cuerpo["choices"][0]
        text = (eleccion.get("message") or {}).get("content") or ""
        uso = cuerpo.get("usage") or {}
        if eleccion.get("finish_reason") == "length":
            # Sin esto, una respuesta truncada llega al parseo como si fuera
            # completa y se confunde con «el modelo no entendio el prompt».
            logger.warning(
                "%s corto por limite de tokens (max_tokens=%d): la respuesta viene "
                "truncada. Si el modelo razona, su pensamiento consume el mismo "
                "presupuesto y hay que subirlo.", self.model, max_tokens,
            )
        entrada, salida = uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)

        self.tracer.generation(
            name=TRACE_LLM_CALL, model=self.model,
            input=user[:LLM_TRUNCATION_LENGTH], output=text[:LLM_TRUNCATION_LENGTH],
            tokens_in=entrada, tokens_out=salida,
            latency_ms=latency_ms, trace_id=trace_id,
        )
        logger.info(
            "LLM call completed: model=%s tokens_in=%d tokens_out=%d latency=%.0fms",
            self.model, entrada, salida, latency_ms,
        )
        return LLMResult(text=text, input_tokens=entrada, output_tokens=salida)

    def _pedir(self, carga: dict) -> dict:
        """La peticion, reintentando lo que vale la pena reintentar.

        `httpx.HTTPTransport(retries=...)` solo cubre fallos de conexion: un 503
        llega como respuesta valida y no se reintenta. Y 503 es justo lo que
        devuelve un free tier cuando el proveedor esta cargado — medido contra
        Gemini: la primera llamada pasa y la siguiente rebota.

        Se reintenta lo transitorio (429 y 5xx) con espera creciente, y se
        respeta `Retry-After` cuando el proveedor lo manda. Un 4xx que no sea
        429 no se reintenta: clave invalida, modelo inexistente o peticion mal
        formada no mejoran por insistir.
        """
        ultimo: Exception | None = None
        for intento in range(self.max_retries + 1):
            try:
                respuesta = self.client.post("/chat/completions", json=carga)
                respuesta.raise_for_status()
                return respuesta.json()
            except httpx.HTTPStatusError as e:
                codigo = e.response.status_code
                # El cuerpo dice mas que el codigo: cuota agotada, modelo
                # inexistente y clave invalida son todos 4xx.
                logger.error(
                    "Error del proveedor (%s): %s", codigo, e.response.text[:300],
                )
                if codigo not in self.perfil.reintenta or intento == self.max_retries:
                    raise
                ultimo = e
                espera = self._espera(e.response, intento, self.perfil)
                logger.warning(
                    "%s devolvio %s: reintento %d/%d en %.1fs",
                    self.model, codigo, intento + 1, self.max_retries, espera,
                )
                time.sleep(espera)
            except Exception as e:
                logger.error("Unexpected LLM error: %s", e)
                raise
        raise ultimo   # pragma: no cover — el bucle sale por return o raise

    @staticmethod
    def _espera(respuesta: httpx.Response, intento: int, perfil: Perfil | None = None) -> float:
        """Lo que pide el proveedor, o espera creciente si no dice nada."""
        a = perfil or perfil_de("")
        cabecera = respuesta.headers.get("Retry-After", "")
        if cabecera.isdigit():
            return min(float(cabecera), a.espera_maxima_s)
        return min(a.espera_base_s * (2 ** intento), a.espera_maxima_s)

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            logger.debug("No se pudo cerrar el cliente HTTP", exc_info=True)
