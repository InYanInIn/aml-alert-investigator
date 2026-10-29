import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastmcp import FastMCP
from langchain.mcp import MCPAdapter
from langgraph.types import Command

from aml_investigator.agent.drafters import scripted_drafter
from aml_investigator.agent.graph import AnalystReview, build_graph, call_tool, open_checkpointer
from aml_investigator.api.queries import Row, alert_page, alert_queue, find_investigation
from aml_investigator.settings import Settings

ANALYST = "analyst"
TEMPLATES = Path(__file__).parent / "templates"


def found[T](row: T | None, kind: str) -> T:
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown {kind}.")
    return row


def see_alert(alert_id: int) -> RedirectResponse:
    return RedirectResponse(f"/alerts/{alert_id}", status_code=303)


def create_app(settings: Settings, *, mcp: str | FastMCP[Any]) -> FastAPI:
    adapter = MCPAdapter(mcp)
    templates = Jinja2Templates(directory=TEMPLATES)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_checkpointer(settings.app_dsn) as checkpointer:
            app.state.graph = build_graph(
                mcp=adapter, drafter=scripted_drafter, app_dsn=settings.app_dsn, checkpointer=checkpointer
            )
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def queue(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "queue.html", {"alerts": await alert_queue(settings.app_dsn)})

    @app.get("/alerts/{alert_id}", response_class=HTMLResponse)
    async def alert_detail(request: Request, alert_id: int) -> HTMLResponse:
        page: Row = found(await alert_page(settings.app_dsn, alert_id), "alert")
        evidence = await call_tool(
            adapter,
            "list_transactions",
            {"alert_id": alert_id, "account_id": page["alert"]["account_id"], "direction": "both"},
        )
        return templates.TemplateResponse(request, "alert.html", {**page, "evidence": evidence})

    @app.post("/alerts/{alert_id}/investigate")
    async def investigate(alert_id: int) -> RedirectResponse:
        found(await alert_page(settings.app_dsn, alert_id), "alert")
        thread_id = f"{alert_id}:{uuid.uuid4().hex}"
        await app.state.graph.ainvoke({"alert_id": alert_id}, {"configurable": {"thread_id": thread_id}})
        return see_alert(alert_id)

    @app.post("/investigations/{investigation_id}/decision")
    async def decide(
        investigation_id: int,
        final_decision: Annotated[Literal["close", "escalate"], Form()],
        comment: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        investigation = found(await find_investigation(settings.app_dsn, investigation_id), "investigation")
        if not investigation["decided"]:
            review = AnalystReview(analyst=ANALYST, final_decision=final_decision, comment=comment)
            await app.state.graph.ainvoke(
                Command(resume=review.model_dump()), {"configurable": {"thread_id": investigation["thread_id"]}}
            )
        return see_alert(investigation["alert_id"])

    return app
