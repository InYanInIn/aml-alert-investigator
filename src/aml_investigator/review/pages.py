import logging
from dataclasses import dataclass
from itertools import batched
from typing import Any

from langchain.mcp import MCPAdapter

from aml_investigator.core.logging import log_context, timed_event
from aml_investigator.investigation.agent.evidence import TRANSACTION_BATCH
from aml_investigator.investigation.agent.tools import ToolCallError, call_tool
from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.verify import cited_txn_ids
from aml_investigator.review.records import (
    Alert,
    AlertSummary,
    AutoClosedAlert,
    InvestigationView,
    QueueItem,
    RuleHit,
)
from aml_investigator.review.repository import ReviewRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertPage:
    alert: Alert
    rule_hits: list[RuleHit]
    investigation: InvestigationView | None
    evidence: dict[str, Any]
    cited_transactions: list[dict[str, Any]]
    cited_passages: list[dict[str, Any]]


class ReviewPages:
    def __init__(self, repository: ReviewRepository, mcp: MCPAdapter) -> None:
        self.repository = repository
        self.mcp = mcp

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

    async def alerts(self) -> list[AlertSummary]:
        with timed_event(logger, "alerts") as event:
            alerts = await self.repository.alerts()
            event["alerts"] = len(alerts)
        return alerts

    async def alert_page(self, alert_id: int) -> AlertPage:
        with log_context(alert_id=alert_id), timed_event(logger, "alert_page"):
            overview = await self.repository.alert_overview(alert_id)
            investigation = await self.repository.latest_investigation(alert_id)
            evidence = await call_tool(
                self.mcp,
                "list_transactions",
                {"alert_id": alert_id, "account_id": overview.alert.account_id, "direction": "both"},
            )
            decision = None if investigation is None else investigation.decision
            return AlertPage(
                alert=overview.alert,
                rule_hits=overview.rule_hits,
                investigation=investigation,
                evidence=evidence,
                cited_transactions=[] if decision is None else await self.cited_transactions(alert_id, decision),
                cited_passages=[] if decision is None else await self.cited_passages(decision),
            )

    async def cited_transactions(self, alert_id: int, decision: Decision) -> list[dict[str, Any]]:
        cited: list[dict[str, Any]] = []
        for batch in batched(sorted(cited_txn_ids(decision)), TRANSACTION_BATCH):
            result = await call_tool(self.mcp, "get_transactions", {"alert_id": alert_id, "txn_ids": list(batch)})
            cited.extend(result["transactions"])
        return cited

    async def cited_passages(self, decision: Decision) -> list[dict[str, Any]]:
        cited = []
        for passage_id in dict.fromkeys(passage_id for claim in decision.claims for passage_id in claim.passage_ids):
            try:
                cited.append(await call_tool(self.mcp, "get_regulation_passage", {"passage_id": passage_id}))
            except ToolCallError:
                logger.warning("cited passage not found", extra={"fields": {"passage_id": passage_id}})
        return cited
