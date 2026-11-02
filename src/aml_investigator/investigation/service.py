import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Literal

from langchain.mcp import MCPAdapter
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from aml_investigator.core.db import open_app_pool
from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.logging import log_context, timed_event
from aml_investigator.investigation.agent.graph import InvestigationGraph, build_graph, postgres_checkpointer
from aml_investigator.investigation.decision import AnalystReview, QaReview
from aml_investigator.investigation.routing import Route
from aml_investigator.review.repository import ReviewRepository

logger = logging.getLogger(__name__)


class QaSampleNotFoundError(NotFoundError):
    def __init__(self, investigation_id: int) -> None:
        super().__init__("QA sample", investigation_id)


@dataclass(frozen=True)
class InvestigationOutcome:
    investigation_id: int
    recommendation: Literal["close", "escalate"]
    verification_status: Literal["passed", "failed"]
    route: Route


class InvestigationService:
    def __init__(
        self,
        *,
        graph: InvestigationGraph,
        repository: ReviewRepository,
        callbacks: Sequence[BaseCallbackHandler] = (),
    ) -> None:
        self.graph = graph
        self.repository = repository
        self.callbacks = list(callbacks)

    async def investigate(self, alert_id: int) -> InvestigationOutcome:
        thread_id = f"{alert_id}:{uuid.uuid4().hex}"
        with log_context(alert_id=alert_id, thread_id=thread_id), timed_event(logger, "investigate") as event:
            await self.repository.alert_overview(alert_id)
            state = await self.graph.ainvoke({"alert_id": alert_id}, self.run_config(thread_id))
            outcome = InvestigationOutcome(
                investigation_id=state["investigation_id"],
                recommendation=state["recommendation"],
                verification_status=state["verification"].status,
                route=state["route"],
            )
            event.update(vars(outcome))
        return outcome

    async def record_decision(self, investigation_id: int, review: AnalystReview) -> int:
        with log_context(investigation_id=investigation_id), timed_event(logger, "record_decision") as event:
            reference = await self.repository.investigation_reference(investigation_id)
            event.update(alert_id=reference.alert_id, thread_id=reference.thread_id, already_decided=reference.decided)
            if not reference.decided:
                with log_context(alert_id=reference.alert_id, thread_id=reference.thread_id):
                    await self.graph.ainvoke(Command(resume=review.model_dump()), self.run_config(reference.thread_id))
        return reference.alert_id

    async def record_qa_review(self, investigation_id: int, review: QaReview) -> int:
        with log_context(investigation_id=investigation_id), timed_event(logger, "record_qa_review") as event:
            reference = await self.repository.investigation_reference(investigation_id)
            if reference.route != "qa_sample":
                raise QaSampleNotFoundError(investigation_id)
            event.update(alert_id=reference.alert_id, already_reviewed=reference.qa_reviewed)
            if not reference.qa_reviewed:
                await self.repository.save_qa_review(investigation_id, review)
        return reference.alert_id

    async def uninvestigated_alerts(self, day: date, limit: int | None = None) -> list[int]:
        return await self.repository.uninvestigated_alerts(day, limit)

    def run_config(self, thread_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": thread_id}, "callbacks": self.callbacks}


@asynccontextmanager
async def open_investigation_service(
    *,
    app_conninfo: str,
    mcp: MCPAdapter,
    model: BaseChatModel,
    callbacks: Sequence[BaseCallbackHandler] = (),
) -> AsyncIterator[InvestigationService]:
    async with open_app_pool(app_conninfo) as pool:
        repository = ReviewRepository(pool)
        graph = build_graph(mcp=mcp, model=model, repository=repository, checkpointer=await postgres_checkpointer(pool))
        yield InvestigationService(graph=graph, repository=repository, callbacks=callbacks)
