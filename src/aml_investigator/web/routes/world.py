from datetime import date, timedelta
from functools import cache
from typing import Annotated, Any, cast

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from aml_investigator.core.db import Pool
from aml_investigator.core.sql import load
from aml_investigator.simulation.resume import WorldInputError
from aml_investigator.simulation.world import CONFIG_DIR, FIRST_DAY, HISTORY_DAYS
from aml_investigator.web.jobs import JobConflictError, SimulationJobs

router = APIRouter(prefix="/api")
type PositiveCount = Annotated[int, Field(gt=0)]


class WorldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = Field(ge=0, le=2**63 - 1)
    persona_counts: dict[str, PositiveCount]


SQL = {
    name: load(__package__, name)
    for name in (
        "world",
        "periods",
        "clients",
        "client_profile",
        "client_flows",
        "client_payments",
        "client_spending",
        "client_alerts",
        "graph",
        "timeline",
    )
}


def day_number(day: date) -> int:
    return (day - FIRST_DAY).days + 1


def selected_date(number: int, latest: int) -> date:
    if number < 1 or number > latest:
        raise HTTPException(status_code=422, detail="Day is outside the published world.")
    return FIRST_DAY + timedelta(days=number - 1)


@cache
def persona_specs() -> dict[str, Any]:
    data = yaml.safe_load((CONFIG_DIR / "personas.yaml").read_text())
    return cast(dict[str, Any], data["personas"])


def default_counts() -> dict[str, int]:
    return {name: spec["accounts"] for name, spec in persona_specs().items()}


@cache
def profile_occupations() -> dict[str, str]:
    data = yaml.safe_load((CONFIG_DIR / "text_pools.yaml").read_text())
    return {occupation: persona for persona, pool in data["profiles"].items() for occupation in pool["occupation"]}


def display_persona(occupation: str | None, income: Any) -> str:
    if occupation is None or income is None:
        return "unknown"
    persona = profile_occupations().get(occupation)
    if persona is None:
        return "unknown"
    band = persona_specs()[persona]["declared_monthly"]
    return persona if band["low"] <= income <= band["high"] else "unknown"


def pool(request: Request) -> Pool:
    result: Pool = request.app.state.world_pool
    return result


def jobs(request: Request) -> SimulationJobs:
    result: SimulationJobs = request.app.state.simulation_jobs
    return result


async def rows(request: Request, name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    async with pool(request).connection() as conn:
        return await (await conn.execute(SQL[name], params)).fetchall()


async def latest_day(request: Request) -> int:
    periods = await rows(request, "periods")
    return max((day_number(period["last_day"]) for period in periods), default=0)


@router.get("/world")
async def world(request: Request) -> dict[str, Any]:
    stored = await rows(request, "world")
    periods = await rows(request, "periods")
    counts = stored[0]["persona_counts"] if stored else default_counts()
    latest = max((day_number(period["last_day"]) for period in periods), default=0)
    return {
        "persona_counts": counts,
        "day": latest,
        "first_live_day": HISTORY_DAYS + 1,
        "thresholds_banner": (
            "thresholds calibrated for 850 accounts; alert volume will differ" if counts != default_counts() else None
        )
        if stored
        else None,
        "spent_usd": jobs(request).spent(),
        "limit_usd": float(jobs(request).settings.ui_spend_limit_usd),
        "periods": [
            {
                **{key: value for key, value in period.items() if key not in ("first_day", "last_day", "target_day")},
                "first_day": day_number(period["first_day"]),
                "last_day": day_number(period["last_day"]),
                "target_day": day_number(period["target_day"]),
                "cost_usd": float(period["cost_usd"]),
            }
            for period in periods
        ],
    }


@router.post("/world", status_code=202)
async def create_world(request: Request, payload: WorldRequest) -> dict[str, str]:
    if set(payload.persona_counts) != set(default_counts()):
        raise HTTPException(status_code=422, detail="Provide a count for every persona.")
    try:
        await jobs(request).create(payload.seed, payload.persona_counts)
    except WorldInputError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except JobConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return {"status": "created"}


@router.get("/world/graph")
async def graph(request: Request, day: int) -> dict[str, Any]:
    end = selected_date(day, await latest_day(request))
    arcs = await rows(request, "graph", {"start": end - timedelta(days=6), "end": end})
    clients = await client_list(request)
    external = sorted(
        {node for arc in arcs for node in (arc["from_client"], arc["to_client"]) if node.startswith("external:")}
    )
    return {
        "clients": [
            {
                "id": c["id"],
                "name": c["name"],
                "persona": c["persona"],
                "blocked_from": day_number(c["blocked_from"])
                if c["blocked_from"] and c["blocked_from"] <= end
                else None,
            }
            for c in clients
        ]
        + [
            {
                "id": node,
                "name": node.removeprefix("external:").replace("_", " ").title(),
                "persona": "external",
                "blocked_from": None,
            }
            for node in external
        ],
        "accounts": [
            {"id": account, "client_id": client["id"]} for client in clients for account in client["accounts"]
        ],
        "arcs": [{**arc, "eur": float(arc["eur"])} for arc in arcs],
    }


@router.get("/world/timeline")
async def timeline(request: Request) -> list[dict[str, Any]]:
    return [{**row, "eur": float(row["eur"])} for row in await rows(request, "timeline")]


@router.get("/clients")
async def client_list(request: Request, persona: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    result = []
    for row in await rows(request, "clients"):
        mapped = {
            "id": row["id"],
            "name": row["name"],
            "persona": display_persona(row["occupation"], row["declared_monthly_eur"]),
            "accounts": row["accounts"],
            "blocked_from": row["blocked_from"],
            "alerts": row["alerts"],
        }
        if (persona is None or mapped["persona"] == persona) and (
            q is None or q.casefold() in str(mapped["name"]).casefold() or q.casefold() in mapped["id"].casefold()
        ):
            result.append(mapped)
    return result


@router.get("/clients/{client_id}")
async def client_detail(request: Request, client_id: str) -> dict[str, Any]:
    profiles = await rows(request, "client_profile", {"client": client_id})
    if not profiles:
        raise HTTPException(status_code=404, detail="Unknown client.")
    profile = profiles[0]
    current = await latest_day(request)
    day = selected_date(current, current)
    params = {"client": client_id, "day": day}
    flows = (await rows(request, "client_flows", params))[0]
    payments = await rows(request, "client_payments", params)
    spending = await rows(request, "client_spending", params)
    alerts = await rows(request, "client_alerts", params)
    return {
        "id": profile["id"],
        "name": profile["name"],
        "persona": display_persona(profile["occupation"], profile["declared_monthly_eur"]),
        "occupation": profile["occupation"],
        "description": profile["description"],
        "purpose": profile["purpose"],
        "expected_activity": profile["expected_activity"],
        "declared_income": float(profile["declared_monthly_eur"]),
        "since": profile["since"],
        "accounts": profile["accounts"],
        "blocked_from": profile["blocked_from"],
        "balance": float(profile["opening_eur"] + flows["incoming"] - flows["outgoing"]),
        "spending": [{**item, "eur": float(item["eur"])} for item in spending],
        "payments": [{**item, "eur": float(item["eur"])} for item in payments],
        "alerts": alerts,
    }
