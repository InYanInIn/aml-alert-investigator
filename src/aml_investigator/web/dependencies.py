from fastapi import Request

from aml_investigator.investigation.service import InvestigationService


def investigation_service(request: Request) -> InvestigationService:
    service: InvestigationService = request.app.state.investigation_service
    return service
