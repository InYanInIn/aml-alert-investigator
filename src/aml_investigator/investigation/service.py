import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from langchain.mcp import MCPAdapter
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from aml_investigator.core.errors import NotFoundError
from aml_investigator.core.logging import log_context, timed_event
from aml_investigator.investigation.decision import AnalystReview
from aml_investigator.investigation.graph import InvestigationGraph
from aml_investigator.investigation.repository import (
    Alert,
    AlertOverview,
    InvestigationReference,
    InvestigationView,
    QueuedAlert,
    ReviewRepository,
    RuleHit,
)
from aml_investigator.investigation.tools import call_tool

logger = logging.getLogger(__name__)


class AlertNotFoundError(NotFoundError):
    def __init__(self, alert_id: int) -> None:
        super().__init__("alert", alert_id)


class InvestigationNotFoundError(NotFoundError):
    def __init__(self, investigation_id: int) -> None:
        super().__init__("investigation", investigation_id)


@dataclass(frozen=True)
class InvestigationOutcome:
    investigation_id: int
    recommendation: Literal["close", "escalate"]
    verification_status: Literal["passed", "failed"]


@dataclass(frozen=True)
class AlertPage:
    alert: Alert
    rule_hits: list[RuleHit]
    investigation: InvestigationView | None
    evidence: dict[str, Any]


class InvestigationService:
    def __init__(self, *, graph: InvestigationGraph, repository: ReviewRepository, mcp: MCPAdapter) -> None:
        self.graph = graph
        self.repository = repository
        self.mcp = mcp

    async def investigate(self, alert_id: int) -> InvestigationOutcome:
        thread_id = f"{alert_id}:{uuid.uuid4().hex}"
        with log_context(alert_id=alert_id, thread_id=thread_id), timed_event(logger, "investigate") as event:
            await self.alert_overview(alert_id)
            state = await self.graph.ainvoke({"alert_id": alert_id}, thread(thread_id))
            outcome = InvestigationOutcome(
                investigation_id=state["investigation_id"],
                recommendation=state["recommendation"],
                verification_status=state["verification"]["status"],
            )
            event.update(vars(outcome))
        return outcome

    async def record_decision(self, investigation_id: int, review: AnalystReview) -> int:
        with log_context(investigation_id=investigation_id), timed_event(logger, "record_decision") as event:
            reference = await self.investigation_reference(investigation_id)
            event.update(alert_id=reference.alert_id, thread_id=reference.thread_id, already_decided=reference.decided)
            if not reference.decided:
                with log_context(alert_id=reference.alert_id, thread_id=reference.thread_id):
                    await self.graph.ainvoke(Command(resume=review.model_dump()), thread(reference.thread_id))
        return reference.alert_id

    async def queue(self) -> list[QueuedAlert]:
        with timed_event(logger, "queue") as event:
            alerts = await self.repository.alert_queue()
            event["alerts"] = len(alerts)
        return alerts

    async def alert_page(self, alert_id: int) -> AlertPage:
        with log_context(alert_id=alert_id), timed_event(logger, "alert_page"):
            overview = await self.alert_overview(alert_id)
            investigation = await self.repository.latest_investigation(alert_id)
            evidence = await call_tool(
                self.mcp,
                "list_transactions",
                {"alert_id": alert_id, "account_id": overview.alert.account_id, "direction": "both"},
            )
        return AlertPage(
            alert=overview.alert, rule_hits=overview.rule_hits, investigation=investigation, evidence=evidence
        )

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


def thread(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}
