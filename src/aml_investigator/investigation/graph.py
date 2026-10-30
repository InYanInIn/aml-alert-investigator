from datetime import datetime
from itertools import pairwise
from typing import Any, Literal, TypedDict

from langchain.mcp import MCPAdapter
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from aml_investigator.core.db import Pool
from aml_investigator.investigation.decision import AnalystReview, Decision
from aml_investigator.investigation.drafters import Drafter, EvidencePack
from aml_investigator.investigation.repository import ReviewRepository
from aml_investigator.investigation.tools import call_tool
from aml_investigator.investigation.verify import Provenance, Verification, verify

MODEL = "scripted"
PROMPT_VERSION = "m1"


class InvestigationState(TypedDict, total=False):
    alert_id: int
    alert: dict[str, Any]
    as_of: str
    transactions: list[dict[str, Any]]
    decision: dict[str, Any]
    verification: dict[str, Any]
    recommendation: Literal["close", "escalate"]
    investigation_id: int
    review: dict[str, Any]


InvestigationGraph = CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]


def build_graph(
    *, mcp: MCPAdapter, drafter: Drafter, repository: ReviewRepository, checkpointer: BaseCheckpointSaver[Any]
) -> InvestigationGraph:
    async def load_alert(state: InvestigationState) -> InvestigationState:
        alert = await call_tool(mcp, "get_alert", {"alert_id": state["alert_id"]})
        return {"alert": alert, "as_of": alert["as_of"]}

    async def build_evidence_pack(state: InvestigationState) -> InvestigationState:
        result = await call_tool(
            mcp,
            "list_transactions",
            {"alert_id": state["alert_id"], "account_id": state["alert"]["account_id"], "direction": "both"},
        )
        return {"transactions": result["transactions"]}

    async def draft_decision(state: InvestigationState) -> InvestigationState:
        decision = await drafter(EvidencePack(alert=state["alert"], transactions=state["transactions"]))
        return {"decision": decision.model_dump()}

    async def verify_citations(state: InvestigationState) -> InvestigationState:
        decision = Decision.model_validate(state["decision"])
        provenance = Provenance(transactions={txn["txn_id"]: txn for txn in state["transactions"]})
        verification = verify(decision, provenance, datetime.fromisoformat(state["as_of"]))
        recommendation = decision.recommendation if verification.status == "passed" else "escalate"
        return {"verification": verification.model_dump(), "recommendation": recommendation}

    async def persist_investigation(state: InvestigationState, config: RunnableConfig) -> InvestigationState:
        investigation_id = await repository.save_investigation(
            alert_id=state["alert_id"],
            thread_id=config["configurable"]["thread_id"],
            model=MODEL,
            prompt_version=PROMPT_VERSION,
            recommendation=state["recommendation"],
            decision=Decision.model_validate(state["decision"]),
            verification=Verification.model_validate(state["verification"]),
        )
        return {"investigation_id": investigation_id}

    async def human_review(state: InvestigationState) -> InvestigationState:
        review = interrupt(
            {
                "investigation_id": state["investigation_id"],
                "recommendation": state["recommendation"],
                "verification_status": state["verification"]["status"],
            },
            response_schema=AnalystReview,
        )
        return {"review": review.model_dump()}

    async def record_decision(state: InvestigationState) -> InvestigationState:
        await repository.save_decision(state["investigation_id"], AnalystReview.model_validate(state["review"]))
        return {}

    builder = StateGraph(InvestigationState)
    builder.add_node(load_alert)
    builder.add_node(build_evidence_pack)
    builder.add_node(draft_decision)
    builder.add_node(verify_citations)
    builder.add_node(persist_investigation)
    builder.add_node(human_review)
    builder.add_node(record_decision)
    for upstream, downstream in pairwise([START, *builder.nodes, END]):
        builder.add_edge(upstream, downstream)
    return builder.compile(checkpointer=checkpointer)


async def postgres_checkpointer(pool: Pool) -> AsyncPostgresSaver:
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    return checkpointer
