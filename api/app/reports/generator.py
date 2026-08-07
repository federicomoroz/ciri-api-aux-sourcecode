"""
HTML report generator using Jinja2 templates.
"""

import json
import logging
import os
from datetime import UTC, datetime

from jinja2 import Environment, FileSystemLoader

from ..data.precomputados import CARTEL, cartel_modelo_gratis
from ..domain.constants import REPORT_TEMPLATE_NAME

logger = logging.getLogger(__name__)


# Dentro de un <script> no rigen las reglas de escape de HTML: lo unico que
# cierra el bloque es la secuencia de cierre literal. Escapando estos caracteres
# a su forma \uXXXX, el bloque no se puede cerrar desde los datos; y como los
# parsers de JSON los devuelven tal cual, el contenido no cambia al recuperarlo.
_ESCAPES_EN_SCRIPT = {c: f"\\u{ord(c):04x}" for c in "<>&"}


def _json_para_html(data: dict) -> str:
    """Los datos del caso, listos para embeber en un <script type="application/json">."""
    crudo = json.dumps(data, ensure_ascii=False, default=str)
    for caracter, escape in _ESCAPES_EN_SCRIPT.items():
        crudo = crudo.replace(caracter, escape)
    return crudo


class ReportGenerator:
    def __init__(self):
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=True,
        )
        self.template = self.env.get_template(REPORT_TEMPLATE_NAME)

    def render(self, data: dict) -> str:
        """Render the HTML report template with all case data.

        No toca el dict que recibe: el llamador suele reusarlo despues.

        El cartel de demo se pasa desde aca y no se escribe en la plantilla para
        que el texto viva en un solo lugar: el mismo que usa el panel.
        """
        generado = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        return self.template.render(
            **data,
            generated_at=generado,
            cartel_demo=_cartel_de(data),
            datos_del_caso=_json_para_html(data),
        )


def _cartel_de(data: dict) -> str:
    """El cartel que corresponde a como se produjo este informe.

    Dos cosas distintas se declaran distinto: una grabacion de una corrida
    anterior, y un analisis recien hecho con un modelo que no es el documentado.
    Sale del dato —la resolucion dice con que modelo corrio— y no de quien pidio
    el informe, porque n8n llama a la API sin pasar por el panel y llegaba
    marcado como prearmado un analisis que se acababa de calcular.
    """
    resolucion = data.get("resolution") or {}
    modelo = resolucion.get("demo_modelo")
    return cartel_modelo_gratis(modelo) if modelo else CARTEL
