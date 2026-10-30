from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from langchain.mcp import MCPAdapter

from aml_investigator.core.db import open_app_pool
from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.logging import configure_logging
from aml_investigator.core.settings import Settings
from aml_investigator.investigation.drafters import scripted_drafter
from aml_investigator.investigation.graph import build_graph, postgres_checkpointer
from aml_investigator.investigation.repository import ReviewRepository
from aml_investigator.investigation.service import InvestigationService
from aml_investigator.web.routes import alerts, investigations


def create_app(settings: Settings, *, mcp: str | FastMCP[Any]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        adapter = MCPAdapter(mcp)
        async with open_app_pool(settings.app_conninfo) as pool:
            repository = ReviewRepository(pool)
            graph = build_graph(
                mcp=adapter,
                drafter=scripted_drafter,
                repository=repository,
                checkpointer=await postgres_checkpointer(pool),
            )
            app.state.investigation_service = InvestigationService(graph=graph, repository=repository, mcp=adapter)
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(alerts.router)
    app.include_router(investigations.router)

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"Unknown {error.kind}."}, status_code=404)

    return app
