from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from aml_investigator.web.dependencies import Pages, Service
from aml_investigator.web.jobs import JobConflictError, RerunStatus
from aml_investigator.web.routes.simulate import simulation_jobs

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
router = APIRouter()
write_router = APIRouter()
rerun_router = APIRouter()
ALERT_LISTS = {
    "alerts": ("/alerts", "All alerts"),
    "queue": ("/", "Queue"),
    "auto-closed": ("/auto-closed", "Auto-closed"),
}


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
async def alert_detail(
    request: Request,
    alert_id: int,
    pages: Pages,
    period: int | None = None,
    source: Literal["alerts", "queue", "auto-closed"] = "alerts",
) -> HTMLResponse:
    back_url, back_label = ALERT_LISTS[source]
    if pages.ui_mode and period is not None and source != "auto-closed":
        back_url = f"{back_url}?period={period}"
    return templates.TemplateResponse(
        request,
        "alert.html",
        {
            "page": await pages.alert_page(alert_id),
            "ui_mode": pages.ui_mode,
            "back_url": back_url,
            "back_label": back_label,
        },
    )


@write_router.post("/alerts/{alert_id}/investigate")
async def investigate(alert_id: int, service: Service) -> RedirectResponse:
    await service.investigate(alert_id)
    return see_alert(alert_id)


@rerun_router.post("/alerts/{alert_id}/rerun", status_code=204)
async def rerun_investigation(
    request: Request, alert_id: int, investigation_id: Annotated[int, Body(embed=True, gt=0)]
) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="The rerun requires a JSON request.")
    try:
        await simulation_jobs(request).rerun_investigation(alert_id, investigation_id)
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@rerun_router.get("/alerts/{alert_id}/rerun-status")
def rerun_status(request: Request, alert_id: int) -> RerunStatus:
    return simulation_jobs(request).rerun_status(alert_id)
