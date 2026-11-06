# ADR-0021: Package layout after M6 — agent, models, review read side, MCP store

Status: accepted · 2026-11-03 · supersedes the package table and request-path layering of ADR-0018

## Context
By M6 the flat `investigation/` package held the agent graph, persistence, the analyst read side and a stand-in
LLM; `mcp_server/server.py` was one 257-line function with nine nested tools; `build_graph` was a 172-line
function of eleven closures over `dict[str, Any]` state; `cli.py` and `evaluation/runner.py` were ~300 lines
each. The cross-cutting rules of ADR-0018 (settings, pools, logging, errors, SQL in files) still hold.

## Decision
| Package | Contents |
|---|---|
| `investigation/agent/` | `state.py` (typed `InvestigationState`), `nodes.py` (`InvestigationNodes`: one method per node), `graph.py` (wiring only, checkpointer), `loop.py` (bounded tool loop), `evidence.py`, `tools.py`, `conversation.py` + `prompts/` |
| `investigation/models/` | `rule_score.py`; M6.6 adds the local model and M7 the Azure model beside it (ADR-0022) |
| `investigation/` | `decision.py`, `verify.py`, `routing.py`, `service.py` (commands only: investigate, record decision, record QA review) |
| `review/` | `records.py` (read models), `repository.py` + `sql/` (all SQL on `review.*`), `pages.py` (`ReviewPages`: queue, auto-closed, alert list, alert page) |
| `mcp_server/` | `schemas.py` (the tool contract), `store.py` (`CaseStore`: queries, `as_of` clamping, argument checks, `CaseDataError`), `tools.py` (nine module-level tools; docstrings are the LLM-facing descriptions), `server.py` (assembly, health routes) |
| `evaluation/` | `runner.py` (the evaluation steps), `results.py` (result files and their JSON shape), the rest unchanged |
| `cli/` | `__init__.py` (`main`), `data.py`, `monitoring.py`, `evaluation.py`, `serve.py`, `context.py` |
| `monitoring/typologies.py` | `Typology` and `RULE_TYPOLOGIES`, shared by the MCP server and the agent |

- **Commands and reads are separate.** Web routes send writes to `InvestigationService` and reads to
  `ReviewPages`; the alert page is the only read that calls MCP.
- **Graph state is typed.** `alert`, `provenance`, `draft`, `decision`, `verification` and `review` are
  pydantic models or dataclasses; the checkpointer's serializer gets them as an explicit allowlist
  (`CHECKPOINTED_TYPES`), so LangGraph never deserializes an unregistered type. `tool_calls` stays plain JSON:
  it is stored as the audit trace exactly as built.
- **Lower layers never import upper ones.** `mcp_server` imports `core` and `monitoring` only.

## Consequences
- No behaviour change: the suite (331 tests) passes; HTML, HTTP codes, stored rows and traces, model messages
  and the MCP tool contract were compared before and after.
- Checkpoints written before this change store those fields as dicts. A thread paused at `human_review` by the
  old code fails when resumed; such escalations are re-investigated rather than resumed.
- `investigation` and `review` depend on each other at package level (the graph writes through the review
  repository; the repository stores the agent's `Decision`), with no module-level import cycle.
