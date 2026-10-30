from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal, TypedDict

import psycopg
from langchain.mcp import MCPAdapter
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from aml_investigator.investigation.decision import Decision
from aml_investigator.investigation.drafters import Drafter, EvidencePack
from aml_investigator.investigation.verify import Provenance, verify

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


class AnalystReview(BaseModel):
    analyst: str
    final_decision: Literal["close", "escalate"]
    comment: str = ""


class ToolCallError(RuntimeError):
    pass


InvestigationGraph = CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]


def build_graph(
    *, mcp: MCPAdapter, drafter: Drafter, app_dsn: str, checkpointer: BaseCheckpointSaver[Any]
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
        thread_id = config["configurable"]["thread_id"]
        async with await psycopg.AsyncConnection.connect(app_dsn, autocommit=True) as conn:
            cursor = await conn.execute(
                "WITH inserted AS ("
                "INSERT INTO review.investigations "
                "(alert_id, thread_id, model, prompt_version, recommendation, decision, verification) "
                "VALUES (%(alert_id)s, %(thread_id)s, %(model)s, %(prompt_version)s, %(recommendation)s, "
                "%(decision)s, %(verification)s) "
                "ON CONFLICT (thread_id) DO NOTHING RETURNING investigation_id) "
                "SELECT investigation_id FROM inserted "
                "UNION ALL SELECT investigation_id FROM review.investigations WHERE thread_id = %(thread_id)s",
                {
                    "alert_id": state["alert_id"],
                    "thread_id": thread_id,
                    "model": MODEL,
                    "prompt_version": PROMPT_VERSION,
                    "recommendation": state["recommendation"],
                    "decision": Jsonb(state["decision"]),
                    "verification": Jsonb(state["verification"]),
                },
            )
            [(investigation_id,)] = await cursor.fetchall()
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
        review = state["review"]
        async with await psycopg.AsyncConnection.connect(app_dsn, autocommit=True) as conn:
            await conn.execute(
                "INSERT INTO review.decisions (investigation_id, analyst, final_decision, comment) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (investigation_id) DO NOTHING",
                (state["investigation_id"], review["analyst"], review["final_decision"], review["comment"]),
            )
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


async def call_tool(mcp: MCPAdapter, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    message = await tools[name].ainvoke({"type": "tool_call", "id": f"{name}-call", "name": name, "args": args})
    if message.status == "error":
        raise ToolCallError(f"MCP tool {name} failed: {message.content}")
    structured: dict[str, Any] = message.artifact["structured_content"]
    return structured


@asynccontextmanager
async def open_checkpointer(app_dsn: str) -> AsyncIterator[AsyncPostgresSaver]:
    dsn = make_conninfo(app_dsn, options="-c search_path=langgraph")
    async with AsyncPostgresSaver.from_conn_string(dsn) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
