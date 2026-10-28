# ADR-0006: MCP as the only data path to the model, via FastMCP and `langchain.mcp`

Status: accepted · 2026-10-29 · revised after the M0 spike

## Context
The model must see only what an analyst tool would expose, and access must be read-only.

The M0 spike found that `langchain-mcp-adapters` 0.3.x fails at import with the official `mcp` SDK v2 (it
imports `mcp.server.fastmcp`, removed in v2). Upstream closed v2 support as superseded by `langchain.mcp`,
shipped in `langchain[mcp]` ≥ 1.4 and built on FastMCP 4, which itself builds on the official `mcp` 2.x
package. `langchain.mcp` is marked beta and warns on import.

Options considered: (A) the official SDK plus ~30 lines of our own MCP-to-tool-calling glue; (B)
`langchain[mcp]` with FastMCP. B was chosen as LangChain's official integration path.

## Decision
- All data the model sees — evidence pack and loop calls — comes through MCP tools.
- Server: FastMCP 4. Client side: `langchain.mcp.MCPAdapter`, which turns MCP tools into LangChain tools.
  One MCP library for both sides.
- Transport: Streamable HTTP in deployment; in-process server in tests.
- Read-only is enforced by the `mcp_reader` Postgres role; tools return compact aggregates with hard limits.
- Tool results arrive as `ToolMessage.artifact.structured_content`; the citation verifier builds its provenance
  set from these artifacts.

## Consequences
- We depend on a beta API. Versions are pinned through `uv.lock`; the spike tests in
  `tests/test_mcp_sdk_spike.py` fail first if an upgrade changes behaviour.
- The beta warning is ignored explicitly in pytest configuration; every other warning is an error.
- One extra network hop per tool call in deployment.
