# ADR-0004: Label isolation by schema, role and time bound

Status: accepted · 2026-10-28

## Context
The evaluation is only honest if the agent cannot see labels directly or through proxies: label columns,
pattern files, transactions after the alert, the post-period tail, or ID ordering.

## Decision
- Labels and patterns live in schema `truth`; only role `evaluator` can read it.
- The MCP server connects as `mcp_reader`; the app as `app`; neither has grants on `truth`.
- Every MCP tool enforces `ts ≤ as_of` from the server-side alert context.
- Post-period tail removed and stable IDs (ADR-0002).
- Automated tests: `permission denied` on `truth` for runtime roles; no MCP response contains truth fields.

## Consequences
- The harness, not the agent, joins decisions with truth after the run.
- Few-shot examples come from the train window only.
