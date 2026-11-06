from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.runnables import Runnable

from aml_investigator.investigation.agent.state import LoopStop
from aml_investigator.investigation.agent.tools import AlertTools, ToolResult
from aml_investigator.investigation.agent.usage import TokenUsage

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


@dataclass(frozen=True)
class LoopBudget:
    tool_calls: int = MAX_TOOL_CALLS
    tokens: int = TOKEN_BUDGET

    def exhausted(self, tool_calls: int, tokens: int) -> Literal["token_budget", "tool_call_limit"] | None:
        if tokens >= self.tokens:
            return "token_budget"
        if tool_calls >= self.tool_calls:
            return "tool_call_limit"
        return None


@dataclass(frozen=True)
class LoopOutcome:
    messages: list[BaseMessage]
    results: list[ToolResult]
    tokens: int
    usage: TokenUsage
    stop: LoopStop


async def investigation_loop(
    model: Runnable[LanguageModelInput, AIMessage],
    tools: AlertTools,
    history: list[BaseMessage],
    *,
    tokens: int,
    usage: TokenUsage,
    budget: LoopBudget,
) -> LoopOutcome:
    added: list[BaseMessage] = []
    results: list[ToolResult] = []
    while tokens < budget.tokens:
        reply = await model.ainvoke([*history, *added])
        tokens += total_tokens(reply)
        usage += TokenUsage.of(reply)
        added.append(reply)
        if not reply.tool_calls:
            return LoopOutcome(added, results, tokens, usage, "model_done")
        for call in reply.tool_calls:
            if budget.exhausted(len(results), tokens) is None:
                results.append(await tools.run(call, "loop"))
                added.append(results[-1].message)
            else:
                added.append(not_executed(call))
        if stop := budget.exhausted(len(results), tokens):
            return LoopOutcome(added, results, tokens, usage, stop)
    return LoopOutcome(added, results, tokens, usage, "token_budget")


def not_executed(call: ToolCall) -> ToolMessage:
    return ToolMessage(content=NOT_EXECUTED, tool_call_id=str(call["id"]), name=call["name"], status="error")


def total_tokens(message: AIMessage) -> int:
    return message.usage_metadata["total_tokens"] if message.usage_metadata else 0
