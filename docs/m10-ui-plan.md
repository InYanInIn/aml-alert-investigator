# M10 Local UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Run and inspect a resumable simulated bank locally, with a visual World and a read-only Analyst desk.

**Architecture:** Reuse the M9 population, tape, daily monitor and investigation service. The local FastAPI app composes a single simulation job, role-restricted reads, existing Analyst pages and a static React/D3 bundle.

**Tech Stack:** Python 3.12, uv, PostgreSQL 16/pgvector, FastAPI, FastMCP, Jinja, React, TypeScript, D3, Vite and Vitest.

**Spec:** `docs/m10-ui-design.md` and the approved clarifications below.

## Global Constraints

- Use only `aml_sim_ui` for the local UI, with one world and one job at a time. Bind the server to loopback. Test with disposable `aml_sim_test_*` databases.
- Keep the M9 CLI, monitoring cadence, tape hash, citations, and simulated analyst policy intact. Only the automatic simulated analyst blocks accounts, from the next day.
- Period 0 has 21 history days and no alerts or cost. Live requests cover 1, 7 or 30 days. Poll every 2 seconds.
- The default system is `azure_profile`. `b4` is free. Use the checked-in `config/thresholds/aml_sim_dev.segments.json`, with a banner on changed persona counts. Keep 850 as the calibration reference, and count actual monitored accounts for estimates.
- No runtime reader gets truth persona, group, scenario, or the seed. The owner stores the seed and the World creation form accepts it, while ordinary API responses omit it. The explicitly enabled evaluator router is separate and its overlay is off by default.
- Keep $0.07 as a display estimate. A durable, owner-only Azure spend ledger survives world reset and day rollback. Reserve a defensible request bound before the provider call, settle known usage, and hold uncertain reservations after interruption. Do not silently repeat uncertain paid calls. UI lifetime limit defaults to $39, and M10 testing may spend at most $5 only after end-to-end B4.
- SQL lives in `.sql` files. No production comments. Use `uv run`, let settings load `.env`, never print secrets, and leave no generated bundle committed. Every commit carries `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Out of scope: AKS, README changes, PR, accounts/auth, mobile-specific layout, live streaming and scenario editing.

## Pre-edit design and change budget

Reuse the existing World/Tape, B4, profile-enabled service, FastAPI factory, Jinja read models and SQL loader. Extend the replay seam before adding the M10-specific job and transport. No legacy public path is retired. Protect deterministic IDs, hashes, as-of evidence, next-day blocks, committed-day visibility, isolated labels and durable spend. The public seams are `DailyLoop`, the existing profile service, the specified HTTP routes and `aml ui`. The cheapest adequate levers are reuse and small refactors for M9, followed by new code for persistence, UI and the HTTP boundary. Initial budget: approximately 1,100–1,700 Python/SQL LOC, 700–1,100 frontend LOC and 350–550 test LOC. No queue framework, new generic ORM, alternate simulator or redesign of the existing review workflows.

### Task 1: Replayable M9 engine

**Files:** `simulation/loop.py`, `simulation/world.py`, `tests/test_simulation_loop.py`.

**Interfaces:** `DailyLoop.replay(days: Iterable[date], blocks: Mapping[date, Sequence[Actor]] | None = None, *, write: bool = False) -> None` replays ordered days, applying blocks after the relevant day. `DailyLoop.advance(days: Iterable[date], system: System, shadow: System, point: OperatingPoint | None, *, stop_on_cap: bool = True) -> None` advances from the existing state. `Tape.append` accepts a missing connection for hash-only replay.

- [ ] Add a test that writes a fixture timeline uninterrupted and separately replays its prefix without inserts, advances the suffix and compares the final hash, transaction IDs and blocks. Run it red.
- [ ] Extract the day generation and monitoring seams, preserving CLI history writes, legacy cadence and early stop. UI cadence is daily. Keep the next transaction ID and digest in the same Tape for replay and advance. Run `uv run pytest -q tests/test_simulation_loop.py tests/test_simulation_world.py tests/test_simulation_small_world.py`.
- [ ] Commit and review against the task base.

### Task 2: Durable periods, job, budget and commands

**Files:** `core/migrations/0007_simulation_ui.sql`, `core/settings.py`, `simulation/{resume,periods}.py` and domain SQL, `web/jobs.py`, `web/routes/simulate.py`, and the smallest changes needed in the existing service/MCP/database composition. Tests in `tests/test_simulation_resume.py` and `tests/test_ui_jobs.py`.

**Interfaces:** `SimulationJobs` exposes create/reset, estimate, start and current status to the HTTP router. `sim.period.last_day` is the last completed day and starts at `first_day - 1`. Define `sim.decision` for B4. Reuse the same M9 profile prompt and MCP profile configuration for live runs and the deterministic model for free tests. Runtime readers receive no seed grant.

- [ ] Use disposable Postgres to drive a seeded B4 world via jobs: 21 history days, 7 live days, second start refused, restart and resume, no duplicates. Run red before implementing migration, repository and deterministic replay.
- [ ] Test crash after day preparation and after investigation persistence. Keep role separation while making completed-day publication atomic; if the existing separate connections are retained, make incomplete-day staging private and recover it idempotently before another run. Test rollback/recovery and hash mismatch. Period error payloads expose safe messages, and structured logs identify period, day and error class.
- [ ] Test estimate and spend guards with a fake provider: a rejected budget performs no calls, a reset preserves spend, a crash leaves the reservation durable, and resume does not issue the uncertain call again. Reserve by stable world generation and alert identity. Clamp calls and token exposure at the provider boundary or refuse paid runs where the bound cannot be established. Check both model and embedding costs.
- [ ] Test prepared case parity with the deterministic profile agent and the read-only role boundary. Run the new suites and existing profile/roles/simulator suites. Commit and review.

### Task 3: World reads, Analyst and local entry point

**Files:** `web/routes/{world,evaluator,alerts}.py`, `web/app.py`, World read SQL, `review/{repository,pages}.py`, `review/sql/{analyst_queue,alerts}.sql`, Jinja templates, `cli/serve.py`, `Makefile` and `.github/workflows/ci.yml`. Tests in `tests/test_ui_api.py`.

**Interfaces:** HTTP schemas and names in the spec, except seed omitted. `GET /api/evaluator/groups` exists only with `--evaluator`. `uv run aml ui` is the local entry point. Keep `aml api` behavior and existing templates valid for non-simulation databases.

- [ ] Drive the public API using TestClient and Postgres: graph aggregates a seven-day window, timeline and client detail match persisted activity, a changed population triggers the banner, period filters isolate Analyst views, B4 and capped alerts have distinguishable states. Assert no truth or seed appears and evaluator is absent by default. Run red.
- [ ] Implement reads under app role and a separately mounted evaluator router. Derive display persona from the onboarding profile only. Keep references as escaped text. Make Analyst commands unavailable in UI mode. Wire local startup, migration, corpus copy if available, and static bundle serving.
- [ ] Wire `make ui` to locked Node installation and build, and `make check` to the repository’s four Python checks plus frontend type check and Vitest. Make CI build and verify the UI. Add ADR-0026 with decisions and security boundaries. Commit and review.

### Task 4: World frontend and visual verification

**Files:** `src/aml_investigator/web/frontend/` and static-build ignore rules. Components: `Graph.tsx`, `Timeline.tsx`, `ClientList.tsx`, `ClientCard.tsx`, `SimulatePanel.tsx`.

**Interfaces:** Consume the spec API and use shared typed responses. Build to `web/static/world/`. Analyst link uses the existing Jinja routes.

- [ ] Test graph aggregation helpers with Vitest, including collapsed external counterparties, signed flows and empty data. Build the World with a functioning Simulate panel, 2-second poll, day timeline and selectable client details.
- [ ] Style World as a dark bank observatory. Use a radial payment network as the main visual, with directional arcs, focus and blocked-node fading. Add clear loading and error states, keyboard-operable controls, and reduced-motion support. Keep data graphics native and avoid decoration that competes with the graph.
- [ ] Run `make ui` and `make check`, then launch and exercise reset, B4 simulate, graph, timeline, client selection and Analyst in the browser. Capture World screenshots after visual verification. Review the whole branch against `ab58886` and leave the local server available for Jan.
