from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from ..dependencies import get_report_generator
from ..domain.models import ReportRequest
from ..reports.generator import ReportGenerator

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.post("/html", response_class=HTMLResponse)
def generate_html_report(
    req: ReportRequest,
    generator: ReportGenerator = Depends(get_report_generator),
) -> str:
    """Generate an HTML report from all case analysis data.
    Returns Content-Type: text/html."""
    html = generator.render(req.model_dump())
    return HTMLResponse(content=html, status_code=200)
