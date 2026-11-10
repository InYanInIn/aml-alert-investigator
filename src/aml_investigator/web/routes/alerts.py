from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aml_investigator.web.dependencies import Pages, Service

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
router = APIRouter()
write_router = APIRouter()


def see_alert(alert_id: int) -> RedirectResponse:
    return RedirectResponse(f"/alerts/{alert_id}", status_code=303)


@router.get("/", response_class=HTMLResponse)
async def queue(request: Request, pages: Pages, period: int | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "items": await pages.analyst_queue(period),
            "periods": await pages.periods(),
            "selected_period": period,
            "ui_mode": pages.ui_mode,
        },
    )


@router.get("/auto-closed", response_class=HTMLResponse)
async def auto_closed(request: Request, pages: Pages) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auto_closed.html",
        {
            "alerts": await pages.auto_closed(),
            "ui_mode": pages.ui_mode,
        },
    )


@router.get("/alerts", response_class=HTMLResponse)
async def all_alerts(request: Request, pages: Pages, period: int | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "alerts.html",
        {
            "alerts": await pages.alerts(period),
            "periods": await pages.periods(),
            "selected_period": period,
            "ui_mode": pages.ui_mode,
        },
    )


@router.get("/alerts/{alert_id}", response_class=HTMLResponse)
async def alert_detail(request: Request, alert_id: int, pages: Pages) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "alert.html",
        {
            "page": await pages.alert_page(alert_id),
            "ui_mode": pages.ui_mode,
        },
    )


@write_router.post("/alerts/{alert_id}/investigate")
async def investigate(alert_id: int, service: Service) -> RedirectResponse:
    await service.investigate(alert_id)
    return see_alert(alert_id)
