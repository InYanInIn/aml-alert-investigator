from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel

from aml_investigator.core.db import open_read_only_pool
from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.logging import configure_logging
from aml_investigator.core.settings import Settings
from aml_investigator.core.tracing import tracing_callbacks
from aml_investigator.investigation.agent.tools import McpUnavailableError
from aml_investigator.investigation.service import open_investigation_service
from aml_investigator.review.pages import ReviewPages
from aml_investigator.web.jobs import SimulationJobs
from aml_investigator.web.routes import alerts, health, investigations, simulate, world
from aml_investigator.web.routes import evaluator as evaluator_routes

WORLD_STATIC_DIR = Path(__file__).parent / "static" / "world"
WORLD_BUILD_MESSAGE = "World UI assets are missing. Run make ui before aml ui."


def create_app(
    settings: Settings,
    *,
    mcp: str | FastMCP[Any],
    model: BaseChatModel,
    ui_mode: bool = False,
    evaluator: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        adapter = MCPAdapter(mcp)
        async with open_investigation_service(
            app_conninfo=settings.app_conninfo,
            mcp=adapter,
            model=model,
            callbacks=tracing_callbacks(settings),
        ) as service:
            app.state.investigation_service = service
            app.state.review_pages = ReviewPages(service.repository, adapter, ui_mode=ui_mode)
            if ui_mode:
                jobs = SimulationJobs(settings)
                app.state.simulation_jobs = jobs
                jobs.recover()
                async with open_read_only_pool(settings.app_conninfo) as world_pool:
                    app.state.world_pool = world_pool
                    if evaluator:
                        async with open_read_only_pool(settings.evaluator_conninfo) as evaluator_pool:
                            app.state.evaluator_pool = evaluator_pool
                            yield
                    else:
                        yield
                await jobs.close()
            else:
                yield

    app = FastAPI(lifespan=lifespan)
    app.include_router(alerts.router)
    if ui_mode:
        app.include_router(alerts.rerun_router)
        app.include_router(world.router)
        app.include_router(simulate.router)
        if evaluator:
            app.include_router(evaluator_routes.router)
        app.mount("/static/world", StaticFiles(directory=WORLD_STATIC_DIR, check_dir=False), name="world-static")

        @app.get("/world")
        async def world_page() -> Response:
            index = WORLD_STATIC_DIR / "index.html"
            if not index.is_file():
                return PlainTextResponse(WORLD_BUILD_MESSAGE, status_code=503)
            return FileResponse(index)
    else:
        app.include_router(alerts.write_router)
        app.include_router(investigations.router)
    app.include_router(health.router)

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": f"Unknown {error.kind}."}, status_code=404)

    @app.exception_handler(McpUnavailableError)
    async def mcp_unavailable(request: Request, _: McpUnavailableError) -> HTMLResponse:
        return alerts.templates.TemplateResponse(request, "unavailable.html", status_code=503)

    return app
