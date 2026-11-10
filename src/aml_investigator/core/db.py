import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, ExitStack, asynccontextmanager
from types import TracebackType
from typing import Literal, Self

from psycopg import AsyncConnection, Connection, Cursor
from psycopg.abc import Params, QueryNoTemplate
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow, RowFactory, TupleRow, dict_row
from psycopg_pool import AsyncConnectionPool

from aml_investigator.core.sql import load

MIN_CONNECTIONS = 1
MAX_CONNECTIONS = 10

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]
SET_ROLE = {"app": load(__package__, "set_app_role"), "mcp": load(__package__, "set_mcp_role")}
RESET_ROLE = load(__package__, "reset_role")


class RoleCursor[Row]:
    def __init__(self, cursor: Cursor[Row]) -> None:
        self._cursor = cursor

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, _error_type: type[BaseException] | None, _error: BaseException | None, _traceback: TracebackType | None
    ) -> None:
        self._cursor.close()

    async def execute(self, query: QueryNoTemplate, params: Params | None = None) -> None:
        self._cursor.execute(query, params)

    async def fetchone(self) -> Row | None:
        return self._cursor.fetchone()

    async def fetchall(self) -> list[Row]:
        return self._cursor.fetchall()


class RoleConnection:
    def __init__(self, conn: Connection[TupleRow], cursors: ExitStack) -> None:
        self._conn = conn
        self._cursors = cursors

    def cursor[Row](self, *, row_factory: RowFactory[Row]) -> RoleCursor[Row]:
        return RoleCursor(self._cursors.enter_context(self._conn.cursor(row_factory=row_factory)))

    async def execute(self, query: QueryNoTemplate, params: Params | None = None) -> RoleCursor[DictRow]:
        cursor = self.cursor(row_factory=dict_row)
        await cursor.execute(query, params)
        return cursor


class FixedRole:
    def __init__(self, conn: Connection[TupleRow], lock: asyncio.Lock, role: Literal["app", "mcp"]) -> None:
        self._conn = conn
        self._lock = lock
        self._role = role

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[RoleConnection]:
        async with self._lock:
            if self._conn.info.transaction_status != TransactionStatus.INTRANS:
                raise RuntimeError("Role queries require an active owner transaction.")
            with self._conn.transaction():
                with self._conn.execute(SET_ROLE[self._role]), ExitStack() as cursors:
                    yield RoleConnection(self._conn, cursors)
                with self._conn.execute(RESET_ROLE):
                    pass


type QuerySource = Pool | FixedRole


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
