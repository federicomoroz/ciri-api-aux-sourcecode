"""Utilidades comunes a los prompts.

El mismo `json.dumps(x, indent=2, ensure_ascii=False)` aparecia siete veces
repartido entre los tres prompts. El formato importa: sin `ensure_ascii=False`
los acentos llegan al modelo como escapes, y sin sangria pierde la estructura
que le pedimos que respete.
"""

from __future__ import annotations

import json
from typing import Any


def bloque_json(valor: Any) -> str:
    """Serializa para incrustar en un prompt: legible y con acentos intactos."""
    return json.dumps(valor, indent=2, ensure_ascii=False)
