import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from itertools import batched
from typing import Any, Literal

from langchain.mcp import MCPAdapter
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from aml_investigator.core.db import open_app_pool
from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.logging import log_context, timed_event
from aml_investigator.investigation.decision import AnalystReview, QaReview
from aml_investigator.investigation.evidence import TRANSACTION_BATCH
from aml_investigator.investigation.graph import InvestigationGraph, build_graph, postgres_checkpointer
from aml_investigator.investigation.repository import (
    Alert,
    AlertOverview,
    AlertSummary,
    AutoClosedAlert,
    InvestigationReference,
    InvestigationView,
    QueueItem,
    ReviewRepository,
    RuleHit,
)
from aml_investigator.investigation.routing import Route
from aml_investigator.investigation.tools import ToolCallError, call_tool
from aml_investigator.investigation.verify import cited_txn_ids

logger = logging.getLogger(__name__)


class AlertNotFoundError(NotFoundError):
    def __init__(self, alert_id: int) -> None:
        super().__init__("alert", alert_id)


class InvestigationNotFoundError(NotFoundError):
    def __init__(self, investigation_id: int) -> None:
        super().__init__("investigation", investigation_id)


class QaSampleNotFoundError(NotFoundError):
    def __init__(self, investigation_id: int) -> None:
        super().__init__("QA sample", investigation_id)


@dataclass(frozen=True)
class InvestigationOutcome:
    investigation_id: int
    recommendation: Literal["close", "escalate"]
    verification_status: Literal["passed", "failed"]
    route: Route


@dataclass(frozen=True)
class AlertPage:
    alert: Alert
    rule_hits: list[RuleHit]
    investigation: InvestigationView | None
    evidence: dict[str, Any]
    cited_transactions: list[dict[str, Any]]
    cited_passages: list[dict[str, Any]]


class InvestigationService:
    def __init__(
        self,
        *,
        graph: InvestigationGraph,
        repository: ReviewRepository,
        mcp: MCPAdapter,
        callbacks: Sequence[BaseCallbackHandler] = (),
    ) -> None:
        self.graph = graph
        self.repository = repository
        self.mcp = mcp
        self.callbacks = list(callbacks)

    async def investigate(self, alert_id: int) -> InvestigationOutcome:
        thread_id = f"{alert_id}:{uuid.uuid4().hex}"
        with log_context(alert_id=alert_id, thread_id=thread_id), timed_event(logger, "investigate") as event:
            await self.alert_overview(alert_id)
            state = await self.graph.ainvoke({"alert_id": alert_id}, self.run_config(thread_id))
            outcome = InvestigationOutcome(
                investigation_id=state["investigation_id"],
                recommendation=state["recommendation"],
                verification_status=state["verification"]["status"],
                route=state["route"],
            )
            event.update(vars(outcome))
        return outcome

    async def record_decision(self, investigation_id: int, review: AnalystReview) -> int:
        with log_context(investigation_id=investigation_id), timed_event(logger, "record_decision") as event:
            reference = await self.investigation_reference(investigation_id)
            event.update(alert_id=reference.alert_id, thread_id=reference.thread_id, already_decided=reference.decided)
            if not reference.decided:
                with log_context(alert_id=reference.alert_id, thread_id=reference.thread_id):
                    await self.graph.ainvoke(Command(resume=review.model_dump()), self.run_config(reference.thread_id))
        return reference.alert_id

    async def record_qa_review(self, investigation_id: int, review: QaReview) -> int:
        with log_context(investigation_id=investigation_id), timed_event(logger, "record_qa_review") as event:
            reference = await self.investigation_reference(investigation_id)
            if reference.route != "qa_sample":
                raise QaSampleNotFoundError(investigation_id)
            event.update(alert_id=reference.alert_id, already_reviewed=reference.qa_reviewed)
            if not reference.qa_reviewed:
                await self.repository.save_qa_review(investigation_id, review)
        return reference.alert_id

    async def alerts(self) -> list[AlertSummary]:
        with timed_event(logger, "alerts") as event:
            alerts = await self.repository.alerts()
            event["alerts"] = len(alerts)
        return alerts

    async def analyst_queue(self) -> list[QueueItem]:
        with timed_event(logger, "analyst_queue") as event:
            items = await self.repository.analyst_queue()
            event["items"] = len(items)
        return items

    async def auto_closed(self) -> list[AutoClosedAlert]:
        with timed_event(logger, "auto_closed") as event:
            alerts = await self.repository.auto_closed()
            event["alerts"] = len(alerts)
        return alerts

    async def uninvestigated_alerts(self, day: date, limit: int | None = None) -> list[int]:
        return await self.repository.uninvestigated_alerts(day, limit)

    async def alert_page(self, alert_id: int) -> AlertPage:
        with log_context(alert_id=alert_id), timed_event(logger, "alert_page"):
            overview = await self.alert_overview(alert_id)
            investigation = await self.repository.latest_investigation(alert_id)
            evidence = await call_tool(
                self.mcp,
                "list_transactions",
                {"alert_id": alert_id, "account_id": overview.alert.account_id, "direction": "both"},
            )
            cited_transactions = [] if investigation is None else await self.transactions(alert_id, investigation)
            cited_passages = [] if investigation is None else await self.passages(investigation)
        return AlertPage(
            alert=overview.alert,
            rule_hits=overview.rule_hits,
            investigation=investigation,
            evidence=evidence,
            cited_transactions=cited_transactions,
            cited_passages=cited_passages,
        )

    async def transactions(self, alert_id: int, investigation: InvestigationView) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for batch in batched(sorted(cited_txn_ids(investigation.decision)), TRANSACTION_BATCH):
            result = await call_tool(self.mcp, "get_transactions", {"alert_id": alert_id, "txn_ids": list(batch)})
            selected.extend(result["transactions"])
        return selected

    async def passages(self, investigation: InvestigationView) -> list[dict[str, Any]]:
        cited = dict.fromkeys(passage_id for claim in investigation.decision.claims for passage_id in claim.passage_ids)
        passages = []
        for passage_id in cited:
            try:
                passages.append(await call_tool(self.mcp, "get_regulation_passage", {"passage_id": passage_id}))
            except ToolCallError:
                logger.warning("cited passage not found", extra={"fields": {"passage_id": passage_id}})
        return passages

    async def alert_overview(self, alert_id: int) -> AlertOverview:
        overview = await self.repository.alert_overview(alert_id)
        if overview is None:
            raise AlertNotFoundError(alert_id)
        return overview

    async def investigation_reference(self, investigation_id: int) -> InvestigationReference:
        reference = await self.repository.investigation_reference(investigation_id)
        if reference is None:
            raise InvestigationNotFoundError(investigation_id)
        return reference

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
        yield InvestigationService(graph=graph, repository=repository, mcp=mcp, callbacks=callbacks)
