from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastmcp import FastMCP
from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel

from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.logging import configure_logging
from aml_investigator.core.settings import Settings
from aml_investigator.core.tracing import tracing_callbacks
from aml_investigator.investigation.service import open_investigation_service
from aml_investigator.investigation.tools import McpUnavailableError
from aml_investigator.web.routes import alerts, health, investigations


def create_app(settings: Settings, *, mcp: str | FastMCP[Any], model: BaseChatModel) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        async with open_investigation_service(
            app_conninfo=settings.app_conninfo,
            mcp=MCPAdapter(mcp),
            model=model,
            callbacks=tracing_callbacks(settings),
        ) as service:
            app.state.investigation_service = service
            yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(alerts.router)
    app.include_router(investigations.router)
    app.include_router(health.router)

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"Unknown {error.kind}."}, status_code=404)

    @app.exception_handler(McpUnavailableError)
    async def mcp_unavailable(request: Request, _: McpUnavailableError) -> HTMLResponse:
        return alerts.templates.TemplateResponse(request, "unavailable.html", status_code=503)

    return app
