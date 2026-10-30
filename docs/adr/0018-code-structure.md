# ADR-0018: Code structure — domain packages, layers where there are callers, SQL in files

Status: accepted · 2026-10-30

## Context
After M2 the package was flat; web routes mixed HTTP with workflow logic; four places opened a database
connection per call; there was no logging; large SQL lived in Python strings. M3 adds a second caller of the
"investigate an alert" use case (the evaluation harness), so a service layer is now justified rather than
speculative.

## Decision

### Packages (by domain)
| Package | Contents | Was |
|---|---|---|
| `core/` | `settings.py` (pydantic-settings), `db.py` (connection pools), `logging.py` (JSON logs + context), `errors.py` (domain error base), `sql.py` (load `.sql` files), `migrate.py`, `migrations/*.sql` | `settings.py`, `migrate.py`, `migrations/` |
| `data/` | `ingest.py`, `fx.py` (exchange rates, split out of ingest), `splits.py` | `ingest.py`, `splits.py` |
| `monitoring/` | `rules/` (`__init__.py` registry, `r1…r6`, `sql/`), `alerts.py` + `sql/`, `calibration.py` + `sql/` | `monitor/`, `calibrate.py` |
| `truth/` | `alert_truth.py` + `sql/` — evaluator only, never imported by runtime code | `truth.py` |
| `investigation/` | `decision.py`, `verify.py`, `drafters.py`, `graph.py` (domain), `repository.py`, `service.py` | `agent/`, parts of `api/` |
| `mcp_server/` | `server.py` + `sql/` | `mcp_server.py` |
| `web/` | `app.py` (factory, lifespan, exception handlers), `dependencies.py`, `routes/alerts.py`, `routes/investigations.py`, `templates/` | `api/` |
| `cli.py` | argument parsing → calls services and pipeline functions | `cli.py` |

### Layering rules
- **Request path** (web, evaluation, CLI → investigation): `web/routes` are thin translators (parse →
  call service → render). `investigation/service.py` owns use cases (`investigate`, `record_decision`, queue
  and alert views). `investigation/repository.py` owns all SQL on `review.*` and the alert read models. Graph
  nodes write through the repository, not raw connections.
- **Batch pipeline** (data, monitoring, truth): plain modules of functions; SQL is the logic and lives in
  `.sql` files next to the module. No repository or service wrappers.
- **Contracts**: `typing.Protocol` only where two real implementations exist (rules, drafters).
- **Dependencies point inward**: `web` → `investigation` → `core`; `investigation` never imports `web`;
  nothing runtime imports `truth`.

### Cross-cutting
- **Settings**: `pydantic-settings`, prefix `AML_`, reads `.env` itself; DSNs are `SecretStr` so they never
  appear in logs or reprs.
- **Connections**: long-running processes (web app, MCP server) use `psycopg_pool` pools opened in their
  lifespan; batch commands open one connection per command.
- **Logging**: stdlib `logging`, one JSON line per event, context fields (`alert_id`, `thread_id`,
  `investigation_id`) bound through `contextvars`. CLI keeps `print` for its user-facing output.
- **Errors**: domain exceptions derive from `core.errors.DomainError` (`AlertNotFound`,
  `InvestigationNotFound`, `ToolCallError`, …); one web exception handler maps "not found" errors to 404.
- **SQL files**: statements longer than a few lines live in `sql/<name>.sql` and are loaded by name with
  `core.sql.load(__package__, name)`; parameters stay `%(name)s` psycopg placeholders.

## Consequences
- Behaviour does not change: the refactor is verified by the existing test suite, whose assertions stay as
  they are (imports and fixtures adapt).
- Readers find code by domain first, then by layer.
- The evaluation harness (M3) calls `investigation.service` directly, with no HTTP and no duplicated logic.
