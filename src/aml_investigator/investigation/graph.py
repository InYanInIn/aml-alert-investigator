import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Literal, TypedDict, cast

from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from aml_investigator.core.db import Pool
from aml_investigator.investigation.conversation import (
    PROMPT_VERSION,
    draft_request,
    investigate_request,
    repair_request,
    system_message,
)
from aml_investigator.investigation.decision import AnalystReview, Decision
from aml_investigator.investigation.evidence import alert_call, evidence_calls, regulation_calls
from aml_investigator.investigation.repository import ReviewRepository
from aml_investigator.investigation.routing import AUTO_CLOSE_ANALYST, Route, route_for
from aml_investigator.investigation.tools import AlertTools, Phase, ToolCallError, ToolResult
from aml_investigator.investigation.verify import Provenance, Verification, verify

MAX_TOOL_CALLS = 8
TOKEN_BUDGET = 40_000
LOOP_TOOLS = (
    "list_transactions",
    "get_transactions",
    "get_account_profile",
    "get_counterparties",
    "trace_funds",
    "search_regulations",
    "get_regulation_passage",
)
NOT_EXECUTED = "Not executed: the investigation budget is exhausted."
NO_DECISION = Decision(recommendation="escalate", typologies=[], who=[], what=[], when=[], where=[], why=[], how=[])

LoopStop = Literal["skipped", "model_done", "tool_call_limit", "token_budget"]


class InvestigationState(TypedDict, total=False):
    alert_id: int
    alert: dict[str, Any]
    as_of: str
    messages: list[BaseMessage]
    provenance: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    loop_stop: LoopStop
    tokens: int
    draft: dict[str, Any] | None
    draft_output: str
    draft_errors: list[str]
    repairs: int
    verification: dict[str, Any]
    decision: dict[str, Any]
    recommendation: Literal["close", "escalate"]
    route: Route
    investigation_id: int
    review: dict[str, Any]


InvestigationGraph = CompiledStateGraph[InvestigationState, None, InvestigationState, InvestigationState]


@dataclass(frozen=True)
class LoopOutcome:
    messages: list[BaseMessage]
    results: list[ToolResult]
    tokens: int
    stop: LoopStop


def build_graph(
    *,
    mcp: MCPAdapter,
    model: BaseChatModel,
    repository: ReviewRepository,
    checkpointer: BaseCheckpointSaver[Any],
    max_tool_calls: int = MAX_TOOL_CALLS,
    token_budget: int = TOKEN_BUDGET,
) -> InvestigationGraph:
    drafter = cast(
        "Runnable[LanguageModelInput, dict[str, Any]]", model.with_structured_output(Decision, include_raw=True)
    )

    async def run_planned(state: InvestigationState, calls: list[ToolCall], phase: Phase) -> InvestigationState:
        tools = await AlertTools.of(mcp, state["alert_id"])
        results = [await tools.run(call, phase) for call in calls]
        return recorded(state, [AIMessage("", tool_calls=calls), *(result.message for result in results)], results)

    async def load_alert(state: InvestigationState) -> InvestigationState:
        tools = await AlertTools.of(mcp, state["alert_id"])
        call = alert_call()
        result = await tools.run(call, "evidence")
        if result.structured is None:
            raise ToolCallError(f"MCP tool get_alert failed: {result.message.text}")
        fresh: InvestigationState = {
            "messages": [system_message()],
            "provenance": asdict(Provenance()),
            "tool_calls": [],
            "tokens": 0,
            "repairs": 0,
        }
        return {
            **fresh,
            **recorded(fresh, [AIMessage("", tool_calls=[call]), result.message], [result]),
            "alert": result.structured,
            "as_of": result.structured["as_of"],
        }

    async def build_evidence_pack(state: InvestigationState) -> InvestigationState:
        return await run_planned(state, evidence_calls(state["alert"]), "evidence")

    async def investigate(state: InvestigationState) -> InvestigationState:
        if max_tool_calls == 0:
            return {"loop_stop": "skipped"}
        tools = await AlertTools.of(mcp, state["alert_id"], LOOP_TOOLS)
        request = investigate_request(max_tool_calls)
        outcome = await investigation_loop(
            model.bind_tools(tools.schemas()),
            tools,
            [*state["messages"], request],
            tokens=state["tokens"],
            max_tool_calls=max_tool_calls,
            token_budget=token_budget,
        )
        return {
            **recorded(state, [request, *outcome.messages], outcome.results),
            "tokens": outcome.tokens,
            "loop_stop": outcome.stop,
        }

    async def retrieve_regulations(state: InvestigationState) -> InvestigationState:
        return await run_planned(state, regulation_calls(state["alert"]), "regulations")

    async def draft_decision(state: InvestigationState) -> InvestigationState:
        output = await drafter.ainvoke([*state["messages"], draft_request()])
        return drafted(state, output)

    async def verify_citations(state: InvestigationState) -> InvestigationState:
        draft = state["draft"]
        verification = (
            Verification(status="failed", errors=state["draft_errors"])
            if draft is None
            else verify(
                Decision.model_validate(draft),
                Provenance(**state["provenance"]),
                datetime.fromisoformat(state["as_of"]),
            )
        )
        return {"verification": verification.model_dump()}

    async def repair(state: InvestigationState) -> InvestigationState:
        request = repair_request(state["draft_output"], state["verification"]["errors"])
        output = await drafter.ainvoke([*state["messages"], draft_request(), request])
        return {**drafted(state, output), "repairs": state["repairs"] + 1}

    async def persist_investigation(state: InvestigationState, config: RunnableConfig) -> InvestigationState:
        verification = Verification.model_validate(state["verification"])
        draft = state["draft"]
        decision = NO_DECISION if draft is None else Decision.model_validate(draft)
        recommendation = decision.recommendation if verification.status == "passed" else "escalate"
        alert = state["alert"]
        route = route_for(
            recommendation, verification.status, alert["account_id"], date.fromisoformat(alert["alert_date"])
        )
        investigation_id = await repository.save_investigation(
            alert_id=state["alert_id"],
            thread_id=config["configurable"]["thread_id"],
            model=model.get_name(),
            prompt_version=PROMPT_VERSION,
            recommendation=recommendation,
            decision=decision,
            verification=verification,
            route=route,
            trace={
                "tool_calls": state["tool_calls"],
                "loop_stop": state["loop_stop"],
                "tokens": state["tokens"],
                "repairs": state["repairs"],
            },
        )
        return {
            "investigation_id": investigation_id,
            "decision": decision.model_dump(),
            "recommendation": recommendation,
            "route": route,
        }

    def route(state: InvestigationState) -> Literal["auto_close", "human_review"]:
        return "human_review" if state["route"] == "escalated" else "auto_close"

    async def auto_close(state: InvestigationState) -> InvestigationState:
        review = AnalystReview(analyst=AUTO_CLOSE_ANALYST, final_decision="close")
        await repository.save_decision(state["investigation_id"], review)
        return {"review": review.model_dump()}

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

    def after_verification(state: InvestigationState) -> Literal["repair", "persist_investigation"]:
        failed = state["verification"]["status"] == "failed"
        return "repair" if failed and state["repairs"] == 0 else "persist_investigation"

    builder = StateGraph(InvestigationState)
    for node in (
        load_alert,
        build_evidence_pack,
        investigate,
        retrieve_regulations,
        draft_decision,
        verify_citations,
        repair,
        persist_investigation,
        auto_close,
        human_review,
        record_decision,
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
    builder.add_conditional_edges("persist_investigation", route)
    builder.add_edge("auto_close", END)
    builder.add_edge("human_review", "record_decision")
    builder.add_edge("record_decision", END)
    return builder.compile(checkpointer=checkpointer)


async def investigation_loop(
    model: Runnable[LanguageModelInput, AIMessage],
    tools: AlertTools,
    history: list[BaseMessage],
    *,
    tokens: int,
    max_tool_calls: int,
    token_budget: int,
) -> LoopOutcome:
    added: list[BaseMessage] = []
    results: list[ToolResult] = []
    while tokens < token_budget:
        reply = await model.ainvoke([*history, *added])
        tokens += total_tokens(reply)
        added.append(reply)
        if not reply.tool_calls:
            return LoopOutcome(added, results, tokens, "model_done")
        for call in reply.tool_calls:
            if len(results) >= max_tool_calls or tokens >= token_budget:
                added.append(not_executed(call))
            else:
                results.append(await tools.run(call, "loop"))
                added.append(results[-1].message)
        if len(results) >= max_tool_calls and tokens < token_budget:
            return LoopOutcome(added, results, tokens, "tool_call_limit")
    return LoopOutcome(added, results, tokens, "token_budget")


def recorded(
    state: InvestigationState, messages: Sequence[BaseMessage], results: Sequence[ToolResult]
) -> InvestigationState:
    provenance = Provenance(**state["provenance"])
    for result in results:
        if result.structured is not None:
            provenance = provenance.including(result.tool, result.structured)
    return {
        "messages": [*state["messages"], *messages],
        "provenance": asdict(provenance),
        "tool_calls": [*state["tool_calls"], *(result.trace for result in results)],
    }


def drafted(state: InvestigationState, output: dict[str, Any]) -> InvestigationState:
    raw: AIMessage = output["raw"]
    parsed = output["parsed"]
    previous = json.dumps([call["args"] for call in raw.tool_calls]) if raw.tool_calls else raw.text
    tokens = state["tokens"] + total_tokens(raw)
    if isinstance(parsed, Decision):
        return {"draft": parsed.model_dump(), "draft_output": previous, "draft_errors": [], "tokens": tokens}
    reason = output["parsing_error"] or "no Decision was returned"
    return {
        "draft": None,
        "draft_output": previous,
        "draft_errors": [f"output does not parse into a Decision: {reason}"],
        "tokens": tokens,
    }


def not_executed(call: ToolCall) -> ToolMessage:
    return ToolMessage(content=NOT_EXECUTED, tool_call_id=str(call["id"]), name=call["name"], status="error")


def total_tokens(message: AIMessage) -> int:
    return message.usage_metadata["total_tokens"] if message.usage_metadata else 0


async def postgres_checkpointer(pool: Pool) -> AsyncPostgresSaver:
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    return checkpointer
