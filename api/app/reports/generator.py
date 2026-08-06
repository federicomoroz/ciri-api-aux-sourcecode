"""
HTML report generator using Jinja2 templates.
"""

import logging
import os
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader

from ..domain.constants import REPORT_TEMPLATE_NAME

logger = logging.getLogger(__name__)


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
        """
        generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return self.template.render(**data, generated_at=generado)
