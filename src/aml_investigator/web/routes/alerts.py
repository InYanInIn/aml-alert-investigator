from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aml_investigator.investigation.service import InvestigationService
from aml_investigator.web.dependencies import investigation_service

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
router = APIRouter()

Service = Annotated[InvestigationService, Depends(investigation_service)]


def see_alert(alert_id: int) -> RedirectResponse:
    return RedirectResponse(f"/alerts/{alert_id}", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def queue(request: Request, service: Service) -> HTMLResponse:
    return templates.TemplateResponse(request, "queue.html", {"alerts": await service.queue()})


@router.get("/alerts/{alert_id}", response_class=HTMLResponse)
async def alert_detail(request: Request, alert_id: int, service: Service) -> HTMLResponse:
    return templates.TemplateResponse(request, "alert.html", {"page": await service.alert_page(alert_id)})


@router.post("/alerts/{alert_id}/investigate")
async def investigate(alert_id: int, service: Service) -> RedirectResponse:
    await service.investigate(alert_id)
    return see_alert(alert_id)
