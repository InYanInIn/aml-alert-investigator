from typing import Annotated, Literal

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from aml_investigator.investigation.decision import AnalystReview, QaReview
from aml_investigator.web.routes.alerts import Service, see_alert

ANALYST = "analyst"

router = APIRouter()


class DecisionForm(BaseModel):
    final_decision: Literal["close", "escalate"]
    comment: str = ""


class QaReviewForm(BaseModel):
    outcome: Literal["confirmed", "reopened"]
    comment: str = ""


@router.post("/investigations/{investigation_id}/decision")
async def decide(investigation_id: int, form: Annotated[DecisionForm, Form()], service: Service) -> RedirectResponse:
    review = AnalystReview(analyst=ANALYST, final_decision=form.final_decision, comment=form.comment)
    return see_alert(await service.record_decision(investigation_id, review))


@router.post("/investigations/{investigation_id}/qa-review")
async def qa_review(investigation_id: int, form: Annotated[QaReviewForm, Form()], service: Service) -> RedirectResponse:
    review = QaReview(analyst=ANALYST, outcome=form.outcome, comment=form.comment)
    return see_alert(await service.record_qa_review(investigation_id, review))
