from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain.mcp import MCPAdapter
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from aml_investigator.core.errors import DomainError

Phase = Literal["evidence", "loop", "regulations"]

ALERT_ID = "alert_id"
ROW_FIELDS = {
    "list_transactions": "transactions",
    "get_transactions": "transactions",
    "get_counterparties": "counterparties",
    "trace_funds": "paths",
    "get_typology_candidates": "candidates",
    "search_regulations": "passages",
}


class ToolCallError(DomainError):
    pass


async def call_tool(mcp: MCPAdapter, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    message = await tools[name].ainvoke({"type": "tool_call", "id": f"{name}-call", "name": name, "args": args})
    if message.status == "error":
        raise ToolCallError(f"MCP tool {name} failed: {message.content}")
    structured: dict[str, Any] = message.artifact["structured_content"]
    return structured


@dataclass(frozen=True)
class ToolResult:
    message: ToolMessage
    trace: dict[str, Any]

    @property
    def tool(self) -> str:
        return str(self.trace["tool"])

    @property
    def structured(self) -> dict[str, Any] | None:
        if self.message.status == "error":
            return None
        structured: dict[str, Any] = self.message.artifact["structured_content"]
        return structured


class AlertTools:
    def __init__(self, tools: Sequence[BaseTool], alert_id: int) -> None:
        self.tools = {tool.name: tool for tool in tools}
        self.alert_id = alert_id

    @classmethod
    async def of(cls, mcp: MCPAdapter, alert_id: int, names: Collection[str] | None = None) -> "AlertTools":
        tools = await mcp.list_tools()
        return cls([tool for tool in tools if names is None or tool.name in names], alert_id)

    def schemas(self) -> list[dict[str, Any]]:
        return [without_alert_id(convert_to_openai_tool(tool)) for tool in self.tools.values()]

    async def run(self, call: ToolCall, phase: Phase) -> ToolResult:
        tool = self.tools.get(call["name"])
        args = {name: value for name, value in call["args"].items() if name != ALERT_ID}
        if tool is None:
            message = ToolMessage(
                content=f"Tool {call['name']} is not available.",
                tool_call_id=call["id"],
                name=call["name"],
                status="error",
            )
        else:
            if ALERT_ID in tool.args:
                args[ALERT_ID] = self.alert_id
            message = await tool.ainvoke({**call, "args": args})
        trace: dict[str, Any] = {"phase": phase, "tool": call["name"], "args": args}
        if message.status == "error":
            trace["error"] = message.text
        else:
            trace["rows"] = row_count(call["name"], message.artifact["structured_content"])
        return ToolResult(message, trace)


def without_alert_id(schema: dict[str, Any]) -> dict[str, Any]:
    parameters = schema["function"]["parameters"]
    stripped = {
        **parameters,
        "properties": {name: value for name, value in parameters["properties"].items() if name != ALERT_ID},
        "required": [name for name in parameters.get("required", []) if name != ALERT_ID],
    }
    return {**schema, "function": {**schema["function"], "parameters": stripped}}


def row_count(tool: str, structured: dict[str, Any]) -> int:
    field = ROW_FIELDS.get(tool)
    return 1 if field is None else len(structured[field])
