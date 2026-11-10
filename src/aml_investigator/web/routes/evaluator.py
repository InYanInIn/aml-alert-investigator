from typing import Any

from fastapi import APIRouter, Request

from aml_investigator.core.db import Pool
from aml_investigator.core.sql import load

router = APIRouter(prefix="/api/evaluator")
GROUPS_SQL = load(__package__, "evaluator_groups")


@router.get("/groups")
async def groups(request: Request) -> list[dict[str, Any]]:
    pool: Pool = request.app.state.evaluator_pool
    async with pool.connection() as conn:
        return await (await conn.execute(GROUPS_SQL)).fetchall()
