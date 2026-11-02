from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aml_investigator.core.health import liveness, readiness
from aml_investigator.web.dependencies import Service

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    return await liveness(request)


@router.get("/readyz")
async def readyz(service: Service) -> JSONResponse:
    return await readiness(service.repository.pool)
