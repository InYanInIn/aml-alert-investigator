from typing import Any

from langchain.mcp import MCPAdapter

from aml_investigator.core.errors import DomainError


class ToolCallError(DomainError):
    pass


async def call_tool(mcp: MCPAdapter, name: str, args: dict[str, Any]) -> dict[str, Any]:
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    message = await tools[name].ainvoke({"type": "tool_call", "id": f"{name}-call", "name": name, "args": args})
    if message.status == "error":
        raise ToolCallError(f"MCP tool {name} failed: {message.content}")
    structured: dict[str, Any] = message.artifact["structured_content"]
    return structured
