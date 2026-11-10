import psycopg
from starlette.requests import Request
from starlette.responses import JSONResponse

from aml_investigator.core.db import FixedRole, Pool, QuerySource
from aml_investigator.core.sql import load

PING_SQL = load(__package__, "ping")
PING_TIMEOUT_SECONDS = 2.0


async def liveness(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def readiness(pool: QuerySource | None) -> JSONResponse:
    if pool is not None and not isinstance(pool, FixedRole) and await reachable(pool):
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "database unavailable"}, status_code=503)


async def reachable(pool: Pool) -> bool:
    try:
        async with pool.connection(timeout=PING_TIMEOUT_SECONDS) as conn:
            await conn.execute(PING_SQL)
    except psycopg.Error:
        return False
    return True
