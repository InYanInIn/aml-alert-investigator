from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from aml_investigator.investigation.decision import AnalystReview
from aml_investigator.investigation.service import InvestigationService
from aml_investigator.web.dependencies import investigation_service
from aml_investigator.web.routes.alerts import see_alert

ANALYST = "analyst"

router = APIRouter()


class DecisionForm(BaseModel):
    final_decision: Literal["close", "escalate"]
    comment: str = ""


@router.post("/investigations/{investigation_id}/decision")
async def decide(
    investigation_id: int,
    form: Annotated[DecisionForm, Form()],
    service: Annotated[InvestigationService, Depends(investigation_service)],
) -> RedirectResponse:
    review = AnalystReview(analyst=ANALYST, final_decision=form.final_decision, comment=form.comment)
    return see_alert(await service.record_decision(investigation_id, review))
