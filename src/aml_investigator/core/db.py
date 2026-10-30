from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

MIN_CONNECTIONS = 1
MAX_CONNECTIONS = 10

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


def open_app_pool(app_dsn: str) -> AbstractAsyncContextManager[Pool]:
    return open_pool(app_dsn, name="app", autocommit=True, options="-c search_path=langgraph")


def open_read_only_pool(dsn: str) -> AbstractAsyncContextManager[Pool]:
    return open_pool(dsn, name="read-only", autocommit=False, options="-c default_transaction_read_only=on")


@asynccontextmanager
async def open_pool(dsn: str, *, name: str, autocommit: bool, options: str) -> AsyncIterator[Pool]:
    pool: Pool = AsyncConnectionPool(
        dsn,
        connection_class=AsyncConnection[DictRow],
        kwargs={"autocommit": autocommit, "row_factory": dict_row, "prepare_threshold": 0, "options": options},
        min_size=MIN_CONNECTIONS,
        max_size=MAX_CONNECTIONS,
        name=name,
        open=False,
    )
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()
