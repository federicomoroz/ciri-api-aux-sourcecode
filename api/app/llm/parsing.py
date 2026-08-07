"""JSON parsing utilities for LLM responses.

Single source of truth for stripping markdown code blocks and safely
parsing potentially-malformed JSON returned by the LLM.

**Por que es mas que un `json.loads`.** Los prompts son deterministas y piden una
estructura explicita, asi que en principio cualquier modelo decente deberia poder
cumplirlos. Lo que cambia entre modelos no es si entienden la consigna: es como
envuelven la respuesta. Claude devuelve el JSON pelado; los modelos mas chicos
—y los de razonamiento— lo rodean de prosa, lo meten en un bloque de codigo, le
dejan una coma colgando, o lo envuelven en un objeto con una clave que nadie
pidio.

Cada una de esas formas esta cubierta aca y fijada en
`tests/unit/test_compatibilidad_modelos.py` con las salidas que producen. Es lo
que hace que «sirve con cualquier modelo decente» sea verificable y no una
esperanza: el prompt es el mismo, lo que se adapta es el desenvoltorio.

Ninguna de estas reparaciones adivina contenido. Sacan envoltorio o arreglan
sintaxis que no tiene otra lectura posible; si el modelo no dijo lo que habia
que decir, se devuelve el fallback y los guardrails se ocupan.
"""

import json
import logging
import re

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Los modelos de razonamiento anteponen su cadena de pensamiento. No es un error:
# es su formato, y el JSON viene despues.
_BLOQUES_DE_RAZONAMIENTO = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE
)

# Una coma antes de cerrar es el error de sintaxis mas comun en los modelos
# chicos, y el unico que se puede reparar sin interpretar nada.
_COMA_COLGANDO = re.compile(r",(\s*[}\]])")


def _limpiar(texto: str) -> str:
    """Saca lo que envuelve al JSON sin tocar el JSON."""
    texto = _BLOQUES_DE_RAZONAMIENTO.sub("", texto).strip()
    if texto.startswith("```"):
        lineas = texto.split("\n")
        texto = "\n".join(lineas[1:-1] if lineas[-1].strip() == "```" else lineas[1:])
    return texto.strip()


def _desenvolver(datos, fallback):
    """Un objeto de una sola clave que contiene la lista pedida ES la lista.

    `{"verdicts": [...]}` cuando se pidio un array es la desviacion mas frecuente
    de los modelos chicos: cumplen el contenido y agregan un envoltorio.
    """
    if isinstance(fallback, list) and isinstance(datos, dict) and len(datos) == 1:
        unico = next(iter(datos.values()))
        if isinstance(unico, list):
            logger.info("Respuesta envuelta en un objeto de una clave: se desenvuelve")
            return unico
    return datos


def parse_json_safely(text: str, fallback: dict | list) -> dict | list:
    """El JSON que devolvio el modelo, venga como venga.

    Del intento mas literal al mas tolerante; el primero que funciona gana.
    """
    text = _limpiar(text)
    try:
        return _desenvolver(json.loads(text), fallback)
    except json.JSONDecodeError:
        pass

    # Prosa alrededor: se recorta desde el primer delimitador hasta el ultimo.
    abre, cierra = ("[", "]") if isinstance(fallback, list) else ("{", "}")
    inicio, fin = text.find(abre), text.rfind(cierra) + 1
    # Un objeto envolviendo la lista no empieza con "[": se prueba igual.
    if isinstance(fallback, list) and inicio < 0:
        inicio, fin = text.find("{"), text.rfind("}") + 1

    if inicio >= 0 and fin > inicio:
        recorte = text[inicio:fin]
        for intento in (recorte, _COMA_COLGANDO.sub(r"\1", recorte)):
            try:
                return _desenvolver(json.loads(intento), fallback)
            except json.JSONDecodeError:
                continue
        logger.warning(
            "No se pudo parsear el JSON del modelo (len=%d), se usa el fallback", len(text)
        )
        return fallback

    logger.warning("La respuesta del modelo no tiene estructura JSON (len=%d)", len(text))
    return fallback


def validate_llm_output(
    raw_text: str,
    model: type[BaseModel],
    fallback: dict | list,
) -> dict | list:
    """Parse LLM JSON response and validate against a Pydantic model.

    Returns model_dump() on success. On validation failure, logs WARNING and
    returns the raw parsed dict so guardrails can still operate.
    """
    parsed = parse_json_safely(raw_text, fallback)
    if parsed is fallback and not raw_text.strip():
        return fallback

    try:
        if isinstance(parsed, list):
            validated = [model.model_validate(item) for item in parsed]
            return [v.model_dump() for v in validated]
        validated = model.model_validate(parsed)
        return validated.model_dump()
    except ValidationError as e:
        logger.warning(
            "LLM output failed validation against %s: %s",
            model.__name__, e.errors(),
        )
        return parsed
