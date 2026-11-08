import json
from collections.abc import Sequence
from typing import Any, cast

from langchain.mcp import MCPAdapter
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolCall
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.types import interrupt

from aml_investigator.investigation.agent.conversation import (
    AgentPrompt,
    draft_request,
    investigate_request,
    repair_request,
    system_message,
)
from aml_investigator.investigation.agent.evidence import alert_call, evidence_calls, regulation_calls
from aml_investigator.investigation.agent.loop import LOOP_TOOLS, LoopBudget, investigation_loop, total_tokens
from aml_investigator.investigation.agent.state import InvestigatedAlert, InvestigationState
from aml_investigator.investigation.agent.tools import AlertTools, Phase, ToolCallError, ToolResult
from aml_investigator.investigation.agent.usage import TokenUsage
from aml_investigator.investigation.decision import AnalystReview, Decision
from aml_investigator.investigation.routing import AUTO_CLOSE_ANALYST, route_for
from aml_investigator.investigation.verify import Provenance, Verification, verify, with_canonical_passage_ids
from aml_investigator.mcp_server.schemas import ReferenceVariant
from aml_investigator.review.repository import ReviewRepository

NO_DECISION = Decision(recommendation="escalate", typologies=[], who=[], what=[], when=[], where=[], why=[], how=[])


class InvestigationNodes:
    def __init__(
        self,
        *,
        mcp: MCPAdapter,
        model: BaseChatModel,
        repository: ReviewRepository,
        budget: LoopBudget,
        reference_variant: ReferenceVariant,
        prompt: AgentPrompt,
    ) -> None:
        self.prompt = prompt
        self.mcp = mcp
        self.model = model
        self.drafter = cast(
            "Runnable[LanguageModelInput, dict[str, Any]]", model.with_structured_output(Decision, include_raw=True)
        )
        self.repository = repository
        self.budget = budget
        self.reference_variant = reference_variant

    async def load_alert(self, state: InvestigationState) -> InvestigationState:
        tools = await AlertTools.of(self.mcp, state["alert_id"])
        call = alert_call()
        result = await tools.run(call, "evidence")
        if result.structured is None:
            raise ToolCallError(f"MCP tool get_alert failed: {result.message.text}")
        started: InvestigationState = {
            "messages": [system_message(self.prompt)],
            "provenance": Provenance(),
            "tool_calls": [],
            "tokens": 0,
            "usage": TokenUsage(),
            "repairs": 0,
            "verifications": [],
        }
        return {
            **started,
            **recorded(started, planned_exchange([call], [result]), [result]),
            "alert": InvestigatedAlert.model_validate(result.structured),
        }

    async def build_evidence_pack(self, state: InvestigationState) -> InvestigationState:
        return await self.run_planned(state, evidence_calls(state["alert"]), "evidence")

    async def investigate(self, state: InvestigationState) -> InvestigationState:
        if self.budget.tool_calls == 0:
            return {"loop_stop": "skipped"}
        tools = await AlertTools.of(self.mcp, state["alert_id"], LOOP_TOOLS)
        request = investigate_request(self.budget.tool_calls)
        outcome = await investigation_loop(
            self.model.bind_tools(tools.schemas()),
            tools,
            [*state["messages"], request],
            tokens=state["tokens"],
            usage=state["usage"],
            budget=self.budget,
        )
        return {
            **recorded(state, [request, *outcome.messages], outcome.results),
            "tokens": outcome.tokens,
            "usage": outcome.usage,
            "loop_stop": outcome.stop,
        }

    async def retrieve_regulations(self, state: InvestigationState) -> InvestigationState:
        return await self.run_planned(state, regulation_calls(state["alert"]), "regulations")

    async def draft_decision(self, state: InvestigationState) -> InvestigationState:
        return await self.drafted(state, [*state["messages"], draft_request()])

    async def verify_citations(self, state: InvestigationState) -> InvestigationState:
        provenance = state["provenance"]
        draft = None if state["draft"] is None else with_canonical_passage_ids(state["draft"], provenance)
        verification = (
            Verification(status="failed", errors=state["draft_errors"])
            if draft is None
            else verify(draft, provenance, state["alert"].as_of)
        )
        return {"draft": draft, "verification": verification, "verifications": [*state["verifications"], verification]}

    async def repair(self, state: InvestigationState) -> InvestigationState:
        request = repair_request(state["draft_output"], state["verification"].errors)
        repaired = await self.drafted(state, [*state["messages"], draft_request(), request])
        return {**repaired, "repairs": state["repairs"] + 1}

    async def persist_investigation(self, state: InvestigationState, config: RunnableConfig) -> InvestigationState:
        verification = state["verification"]
        draft = state["draft"]
        decision = NO_DECISION if draft is None else draft
        recommendation = decision.recommendation if verification.status == "passed" else "escalate"
        alert = state["alert"]
        route = route_for(recommendation, verification.status, alert.account_id, alert.alert_date)
        investigation_id = await self.repository.save_investigation(
            alert_id=state["alert_id"],
            thread_id=config["configurable"]["thread_id"],
            model=self.model.get_name(),
            prompt_version=self.prompt.version,
            recommendation=recommendation,
            decision=decision,
            verification=verification,
            route=route,
            trace={
                "reference_variant": self.reference_variant,
                "tool_calls": state["tool_calls"],
                "loop_stop": state["loop_stop"],
                "tokens": state["tokens"],
                "usage": state["usage"].model_dump(),
                "cost_usd": state["usage"].cost_usd(self.model.get_name()),
                "repairs": state["repairs"],
                "verifications": [attempt.model_dump() for attempt in state["verifications"]],
            },
        )
        return {
            "investigation_id": investigation_id,
            "decision": decision,
            "recommendation": recommendation,
            "route": route,
        }

    async def auto_close(self, state: InvestigationState) -> InvestigationState:
        review = AnalystReview(analyst=AUTO_CLOSE_ANALYST, final_decision="close")
        await self.repository.save_decision(state["investigation_id"], review)
        return {"review": review}

    async def human_review(self, state: InvestigationState) -> InvestigationState:
        review = interrupt(
            {
                "investigation_id": state["investigation_id"],
                "recommendation": state["recommendation"],
                "verification_status": state["verification"].status,
            },
            response_schema=AnalystReview,
        )
        return {"review": review}

    async def record_decision(self, state: InvestigationState) -> InvestigationState:
        await self.repository.save_decision(state["investigation_id"], state["review"])
        return {}

    async def run_planned(self, state: InvestigationState, calls: list[ToolCall], phase: Phase) -> InvestigationState:
        tools = await AlertTools.of(self.mcp, state["alert_id"])
        results = [await tools.run(call, phase) for call in calls]
        return recorded(state, planned_exchange(calls, results), results)

    async def drafted(self, state: InvestigationState, messages: list[BaseMessage]) -> InvestigationState:
        output = await self.drafter.ainvoke(messages)
        raw: AIMessage = output["raw"]
        drafted: InvestigationState = {
            "draft_output": json.dumps([call["args"] for call in raw.tool_calls]) if raw.tool_calls else raw.text,
            "tokens": state["tokens"] + total_tokens(raw),
            "usage": state["usage"] + TokenUsage.of(raw),
        }
        if isinstance(output["parsed"], Decision):
            return {**drafted, "draft": output["parsed"], "draft_errors": []}
        reason = output["parsing_error"] or "no Decision was returned"
        return {**drafted, "draft": None, "draft_errors": [f"output does not parse into a Decision: {reason}"]}


def planned_exchange(calls: list[ToolCall], results: Sequence[ToolResult]) -> list[BaseMessage]:
    return [AIMessage("", tool_calls=calls), *(result.message for result in results)]


def recorded(
    state: InvestigationState, messages: Sequence[BaseMessage], results: Sequence[ToolResult]
) -> InvestigationState:
    provenance = state["provenance"]
    for result in results:
        if result.structured is not None:
            provenance = provenance.including(result.tool, result.structured)
    return {
        "messages": [*state["messages"], *messages],
        "provenance": provenance,
        "tool_calls": [*state["tool_calls"], *(result.trace for result in results)],
    }
