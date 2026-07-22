from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from ..dependencies import get_report_generator
from ..domain.models import ReportRequest
from ..reports.generator import ReportGenerator

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/html")
def generate_html_report(
    req: ReportRequest,
    generator: ReportGenerator = Depends(get_report_generator),
) -> dict:
    """Generate an HTML report from all case analysis data.
    Returns JSON with html field for n8n compatibility."""
    html = generator.render(req.model_dump())
    return {"html": html}
