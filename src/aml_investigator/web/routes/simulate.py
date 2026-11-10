from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from aml_investigator.simulation.periods import UiSystem
from aml_investigator.web.jobs import UI_RUN_DAYS, Estimate, JobConflictError, JobStatus, SimulationJobs

router = APIRouter(prefix="/api")


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: Literal[1, 7, 30]
    system: UiSystem = "azure_profile"


def simulation_jobs(request: Request) -> SimulationJobs:
    jobs: SimulationJobs = request.app.state.simulation_jobs
    return jobs


@router.get("/estimate")
def estimate(request: Request, days: int, system: UiSystem = "azure_profile") -> Estimate:
    if days not in UI_RUN_DAYS:
        raise HTTPException(status_code=422, detail="Simulation days must be 1, 7 or 30.")
    return simulation_jobs(request).estimate(days, system)


@router.post("/simulate", status_code=202)
async def simulate(request: Request, run: SimulationRequest) -> dict[str, int]:
    try:
        period_id = simulation_jobs(request).start(run.days, run.system)
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"period_id": period_id}


@router.get("/jobs/current")
def current(request: Request) -> JobStatus | None:
    return simulation_jobs(request).current()
