# ADR-0026: Local simulation UI

Status: accepted · 2026-11-10 · design in `docs/m10-ui-design.md`

## Context

The M9 world can run in batches but has no local visual inspection surface. Its labels must remain hidden from the agent and ordinary Analyst views.

## Decision

- `aml ui` uses the dedicated `aml_sim_ui` database, installs migrations, copies an existing local corpus when available, and binds only to 127.0.0.1.
- The World frontend is a built React and D3 bundle served by FastAPI. World reads use the app role with read-only transactions and published simulation days. External accounts are grouped by a configured kind or `other_external`; no scenario source or sink role is exposed.
- The Analyst pages remain Jinja views with an optional simulation period. They show B4 decisions and capped alerts distinctly. The local UI can rerun the latest Azure investigation for a published alert regardless of verification status. This creates another review record under the same Azure spend limit and leaves published decisions and blocks intact. Ordinary `aml api` retains its separate investigation and review commands.
- The evaluator overlay is a separate router and read-only evaluator connection, mounted only with `--evaluator`. It is absent from ordinary responses and templates.
- One process owns the background simulation job. The durable period and spend ledger support recovery after interruption. The seed and generation identifier are owner-only and never returned by the ordinary API.

## Consequences

The app is a local single-user tool without login. Node is needed to build the frontend; it is not needed at runtime. The UI polls committed day snapshots and cannot mutate Analyst decisions.

From a clean checkout, run `make ui` to build assets, then `uv run aml ui` to start the local app.
