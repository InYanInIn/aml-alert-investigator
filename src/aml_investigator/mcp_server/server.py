from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from aml_investigator.core.db import open_read_only_pool
from aml_investigator.core.health import liveness, readiness
from aml_investigator.corpus.embeddings import QueryEmbedder
from aml_investigator.mcp_server.schemas import ReferenceVariant
from aml_investigator.mcp_server.store import CaseStore
from aml_investigator.mcp_server.tools import TOOLS


def build_server(
    dsn: str, *, reference_variant: ReferenceVariant = "clean", embed_query: QueryEmbedder | None = None
) -> FastMCP[Any]:
    resources: dict[str, CaseStore] = {}

    @asynccontextmanager
    async def lifespan(_: FastMCP[Any]) -> AsyncIterator[dict[str, CaseStore]]:
        async with open_read_only_pool(dsn) as pool:
            resources["store"] = CaseStore(pool, reference_variant, embed_query)
            yield resources

    server: FastMCP[Any] = FastMCP("aml-investigator", lifespan=lifespan)
    server.custom_route("/healthz", methods=["GET"])(liveness)

    @server.custom_route("/readyz", methods=["GET"])
    async def readyz(_: Request) -> JSONResponse:
        store = resources.get("store")
        return await readiness(store.pool if store is not None else None)

    for tool in TOOLS:
        server.tool(tool)
    return server
