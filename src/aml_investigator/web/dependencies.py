from typing import Annotated

from fastapi import Depends, Request

from aml_investigator.investigation.service import InvestigationService
from aml_investigator.review.pages import ReviewPages


def investigation_service(request: Request) -> InvestigationService:
    service: InvestigationService = request.app.state.investigation_service
    return service


def review_pages(request: Request) -> ReviewPages:
    pages: ReviewPages = request.app.state.review_pages
    return pages


Service = Annotated[InvestigationService, Depends(investigation_service)]
Pages = Annotated[ReviewPages, Depends(review_pages)]
