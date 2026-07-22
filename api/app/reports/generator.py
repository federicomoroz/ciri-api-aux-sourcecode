import os
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader


class ReportGenerator:
    def __init__(self):
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=True,
        )
        self.template = self.env.get_template("case_report.html")

    def render(self, data: dict) -> str:
        """Render the HTML report template with all case data."""
        data["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return self.template.render(**data)
