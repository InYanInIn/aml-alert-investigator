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
from aml_investigator.simulation.world import FIRST_DAY

logger = logging.getLogger(__name__)
PERIOD_LENGTHS = {1: "1 day", 7: "1 week", 30: "1 month"}


@dataclass(frozen=True)
class AlertPage:
    alert: Alert
    rule_hits: list[RuleHit]
    investigation: InvestigationView | None
    evidence: dict[str, Any]
    cited_transactions: list[dict[str, Any]]
    cited_passages: list[dict[str, Any]]
    simulation_status: str | None = None


class ReviewPages:
    def __init__(self, repository: ReviewRepository, mcp: MCPAdapter, *, ui_mode: bool = False) -> None:
        self.repository = repository
        self.mcp = mcp
        self.ui_mode = ui_mode

    async def analyst_queue(self, period: int | None = None) -> list[QueueItem]:
        with timed_event(logger, "analyst_queue") as event:
            items = await self.repository.analyst_queue(period)
            event["items"] = len(items)
        return items

    async def auto_closed(self) -> list[AutoClosedAlert]:
        with timed_event(logger, "auto_closed") as event:
            alerts = await self.repository.auto_closed()
            event["alerts"] = len(alerts)
        return alerts

    async def alerts(self, period: int | None = None) -> list[AlertSummary]:
        with timed_event(logger, "alerts") as event:
            alerts = await self.repository.alerts(period)
            event["alerts"] = len(alerts)
        return alerts

    async def periods(self) -> list[dict[str, Any]]:
        if not self.ui_mode:
            return []
        labels = []
        for period in await self.repository.periods():
            first = (period["first_day"] - FIRST_DAY).days + 1
            target = (period["target_day"] - FIRST_DAY).days + 1
            span = str(first) if first == target else f"{first}\u2013{target}"
            length = target - first + 1
            duration = PERIOD_LENGTHS.get(length, f"{length} days")
            labels.append({"period_id": period["period_id"], "label": f"Day {span} ({duration}, {period['system']})"})
        return labels

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
            simulation = await self.repository.simulation_decision(alert_id) if self.ui_mode else None
            simulation_status = None
            if simulation is not None:
                simulation_status = (
                    "rule B4, no prepared case" if simulation["recommendation"] is not None else "not investigated"
                )
            return AlertPage(
                alert=overview.alert,
                rule_hits=overview.rule_hits,
                investigation=investigation,
                evidence=evidence,
                cited_transactions=[] if decision is None else await self.cited_transactions(alert_id, decision),
                cited_passages=[] if decision is None else await self.cited_passages(decision),
                simulation_status=simulation_status,
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
