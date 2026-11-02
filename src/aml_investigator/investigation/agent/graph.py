from typing import Any, Literal

from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from aml_investigator.core.db import Pool
from aml_investigator.investigation.agent.loop import MAX_TOOL_CALLS, TOKEN_BUDGET, LoopBudget
from aml_investigator.investigation.agent.nodes import InvestigationNodes
from aml_investigator.investigation.agent.state import CHECKPOINTED_TYPES, InvestigationState
from aml_investigator.review.repository import ReviewRepository

InvestigationGraph = CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]


def build_graph(
    *,
    mcp: MCPAdapter,
    model: BaseChatModel,
    repository: ReviewRepository,
    checkpointer: BaseCheckpointSaver[Any],
    max_tool_calls: int = MAX_TOOL_CALLS,
    token_budget: int = TOKEN_BUDGET,
) -> InvestigationGraph:
    nodes = InvestigationNodes(
        mcp=mcp, model=model, repository=repository, budget=LoopBudget(tool_calls=max_tool_calls, tokens=token_budget)
    )
    builder = StateGraph(InvestigationState)
    for node in (
        nodes.load_alert,
        nodes.build_evidence_pack,
        nodes.investigate,
        nodes.retrieve_regulations,
        nodes.draft_decision,
        nodes.verify_citations,
        nodes.repair,
        nodes.persist_investigation,
        nodes.auto_close,
        nodes.human_review,
        nodes.record_decision,
    ):
        builder.add_node(node)
    builder.add_edge(START, "load_alert")
    builder.add_edge("load_alert", "build_evidence_pack")
    builder.add_edge("build_evidence_pack", "investigate")
    builder.add_edge("investigate", "retrieve_regulations")
    builder.add_edge("retrieve_regulations", "draft_decision")
    builder.add_edge("draft_decision", "verify_citations")
    builder.add_conditional_edges("verify_citations", after_verification)
    builder.add_edge("repair", "verify_citations")
    builder.add_conditional_edges("persist_investigation", after_persisting)
    builder.add_edge("auto_close", END)
    builder.add_edge("human_review", "record_decision")
    builder.add_edge("record_decision", END)
    return builder.compile(checkpointer=checkpointer)


def after_verification(state: InvestigationState) -> Literal["repair", "persist_investigation"]:
    failed = state["verification"].status == "failed"
    return "repair" if failed and state["repairs"] == 0 else "persist_investigation"


def after_persisting(state: InvestigationState) -> Literal["auto_close", "human_review"]:
    return "human_review" if state["route"] == "escalated" else "auto_close"


async def postgres_checkpointer(pool: Pool) -> AsyncPostgresSaver:
    checkpointer = AsyncPostgresSaver(pool, serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINTED_TYPES))
    await checkpointer.setup()
    return checkpointer
