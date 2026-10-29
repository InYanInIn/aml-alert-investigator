from typing import Any, TypedDict

import pytest
from fastmcp import FastMCP
from langchain.mcp import MCPAdapter
from langchain_core.messages import ToolMessage
from langgraph.graph import START, StateGraph


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_server() -> FastMCP[Any]:
    server: FastMCP[Any] = FastMCP("spike")

    @server.tool
    def count_characters(account_id: str) -> int:
        """Return the length of an account ID."""
        return len(account_id)

    return server


class SpikeState(TypedDict, total=False):
    account_id: str
    character_count: int


@pytest.mark.anyio
async def test_adapter_exposes_mcp_tool_as_langchain_tool() -> None:
    async with MCPAdapter(build_server()) as adapter:
        tools = await adapter.list_tools()
        message = await tools[0].ainvoke(
            {"type": "tool_call", "id": "call-1", "name": "count_characters", "args": {"account_id": "A1234"}}
        )

    assert [tool.name for tool in tools] == ["count_characters"]
    assert tools[0].args == {"account_id": {"type": "string"}}
    assert isinstance(message, ToolMessage)
    assert message.status == "success"
    assert message.artifact == {"structured_content": {"result": 5}}


@pytest.mark.anyio
async def test_langgraph_node_calls_mcp_tool() -> None:
    adapter = MCPAdapter(build_server())

    async def call_tool(state: SpikeState) -> SpikeState:
        tools = {tool.name: tool for tool in await adapter.list_tools()}
        message = await tools["count_characters"].ainvoke(
            {
                "type": "tool_call",
                "id": "call-1",
                "name": "count_characters",
                "args": {"account_id": state["account_id"]},
            }
        )
        return {"character_count": message.artifact["structured_content"]["result"]}

    graph = StateGraph(SpikeState).add_node("call_tool", call_tool).add_edge(START, "call_tool").compile()

    final = await graph.ainvoke({"account_id": "A12"})

    assert final["character_count"] == 3
