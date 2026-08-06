"""
HTML report generator using Jinja2 templates.
"""

import json
import logging
import os
from datetime import UTC, datetime

from jinja2 import Environment, FileSystemLoader

from ..data.precomputados import CARTEL
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
            cartel_demo=CARTEL,
            datos_del_caso=_json_para_html(data),
        )
