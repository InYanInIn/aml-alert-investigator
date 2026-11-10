# M10 — Local UI: design

Status: draft · 2026-11-09

## Goal
A local app for Jan to run the simulated bank from M9 in steps and look at it: a World tab to watch clients, payments
and blocks, and an Analyst tab to read the cases the AI prepared. The app is also what the portfolio and the M11 README
show, so the World tab has to look good.

Out of scope: Azure deployment, README and screenshots (M11), login, several users (one local user, no auth), an
Analyst tab that decides or blocks (blocks come only from the automatic simulated analyst, as in M9), editing personas
or scenarios, live streaming (the UI polls), mobile layout.

## Decisions
| Question | Decision | Reason |
|---|---|---|
| World frontend | React + D3, built with Vite into `web/static/world/`, served by FastAPI as static files | One page with a graph, a timeline and a card; D3 draws arcs and fades |
| Analyst frontend | Existing Jinja pages, one added filter | Plain and working is the brief; the pages exist |
| Node | Needed only to build; the built bundle is committed-free (CI and `make ui` build it) | No Node at run time |
| World state | One database `aml_sim_ui`, one world at a time, resumable | `require_simulation_database` already guards `aml_sim_*` |
| System during a UI run | `azure_profile` by default, `b4` as a free option in the Simulate panel | The profile agent is what the UI shows; B4 allows free clicking |
| Jobs | One background job at a time, in the FastAPI process, polled | Local, single user; no queue |

## Resumable world
Everything that defines the world is a seed plus settings plus blocks; the tape is a function of them (ADR-0002).

Migration `0007` adds schema `sim` (owner and `aml_app` read, `aml_evaluator` read; not readable by `aml_mcp_reader`):
- `sim.world (only_row, seed, persona_counts jsonb, thresholds_source, system, created_at)`. One row.
- `sim.period (period_id, first_day, last_day, system, investigation_cap, cost_usd, investigations, status, started_at,
  finished_at, tape_sha256)`. `status` is `running`, `done` or `failed`.
- `sim.block (account_id, decided_on, period_id)`: the analyst's blocks, applied from `decided_on + 1`.
- `sim.alert_period (alert_id, period_id)`: which period produced an alert.

A **simulation period** is one click of Simulate: the days it added (1, 7 or 30) and the money it spent. The Analyst
filter lists periods as "Day 22–28 (1 week, azure_profile)". Periods are consecutive; the 21 history days form period 0,
generated when the world is created, with no alerts and no cost.

**Resume.** `aml ui` and the job start from the stored seed. The days up to `max(last_day)` are replayed in memory by
the same `Tape`, `Balances` and `Laundering` objects as M9, with a `Tape` that hashes instead of inserting, and the stored
blocks applied on their dates. The resulting hash must equal the last `period.tape_sha256`; otherwise the job refuses
with "world no longer reproducible". Only the new days are inserted, together with their alerts and decisions. Replay
needs no database writes and no Azure call; its cost is seconds, not money.

**New world.** The panel lets Jan set the seed and a count per persona (defaults: 200 / 200 / 200 / 150 / 80, plus 20
launderers by scenario, which are not editable). Starting a new world wipes the `sim.*` and world tables after a confirmation
that names the days and dollars lost, then generates history.

**Thresholds.** The calibrated segment thresholds of the M9 development world are copied into `aml_sim_ui` for every world
(`sim.world.thresholds_source`). When the persona counts differ from the defaults, the World tab shows the banner
"thresholds calibrated for 850 accounts; alert volume will differ". There is no recalibration in the UI: it needs the
train window and costs a B1 pass.

## Long runs
`POST /api/simulate` validates, creates a `running` period and starts one asyncio task, which advances day by day through
the M9 `DailyLoop`, split into `replay(days)` and `advance(days)` (`run` becomes `replay(history)` plus `advance(live)`, so
`aml sim run` produces the same hash). Each day is one transaction: tape rows, alerts, decisions, blocks, spend. The job
updates `period.last_day`, `investigations` and `cost_usd` per day; the UI polls `GET /api/jobs/current` every 2 s.

A second request while a job runs gets `409`. A failure (Azure error, process killed) rolls back the open day, sets the
period to `failed` with the last full day, and leaves the world resumable: the next Simulate starts after that day. On
startup, a stale `running` period is set to `failed` the same way. Alerts past the cap stay undecided and are marked "not
investigated" in the Analyst list; the period is shown as truncated, as in M9.

## Money
- **Estimate before running**: expected alerts = 2 per day (the calibration budget) scaled by monitored accounts over 850,
  times $0.07 per investigation (M9: about $4 for 60). `GET /api/estimate` returns it; the button shows "about $4.00,
  at most 60 investigations". `b4` shows $0.
- **Cap per run**: `min(60, ceil(1.5 * expected alerts))`, using `MAX_INVESTIGATIONS`. Not user-editable.
- **Lifetime guard**: `Settings.ui_spend_limit_usd = 39.0`, from about $69 of Azure credit left minus $30 kept for AKS.
  `sim.period.cost_usd` summed is the spend; a run is refused when spent plus the cap times $0.07 would pass the limit.
  The header always shows "spent $x of $39". Raising the limit means editing the setting; no UI for it.

## Label isolation (ADR-0004)
The agent, the MCP reader, the Analyst pages and all `/api/world/*` endpoints never read `truth.sim_actors`, persona or group.
The World tab does need personas to group clients: it uses a **display persona**, which is derived from the customer
profile (occupation, declared income) by the same rule a bank would use at onboarding, not from `sim_actors`. Launderers
hidden among them look like their persona.

The god-view is off by default. A toggle "Show laundering groups" calls `GET /api/evaluator/groups`, served from a
separate router that connects as `aml_evaluator`, is mounted only when the app starts with `--evaluator`, and returns
`{account_id, group_id, scenario, first_payment_day, blocked_on}`. The toggle outlines those accounts and nothing else
changes. The Analyst tab has no such toggle.

## JSON API (all under `/api`, read-only except `simulate` and `world`)
- `GET /world`: `{seed, persona_counts, day, first_live_day, thresholds_banner, spent_usd, limit_usd, periods[]}`.
- `GET /world/graph?day=D`: `{clients[{id, name, persona, blocked_from}], accounts[{id, client_id}],
  arcs[{from_client, to_client, count, eur}]}` for the week ending D; counterparties outside the bank are one node per
  kind. Arcs are aggregated server-side, so a month stays under about 3,000.
- `GET /world/timeline`: `[{week, payments, eur, alerts, blocks}]`.
- `GET /clients?persona=&q=`: list `{id, name, persona, accounts, blocked_from, alerts}`.
- `GET /clients/{id}`: profile (occupation, description, purpose, expected activity, declared income, since), balance,
  spending by category per week, recent payments with references (as untrusted text), alerts.
- `GET /estimate?days=1|7|30&system=`: `{expected_alerts, cap, cost_usd, within_limit}`.
- `POST /simulate {days, system}` returns `{period_id}` or `409`. `POST /world {seed, persona_counts}` returns `202`.
- `GET /jobs/current`: `{period_id, status, day, days_total, investigations, cost_usd, error}`.
- `GET /evaluator/groups`: see above.

Analyst pages: `GET /?period=<id>` (queue) and `GET /alerts?period=<id>` take the filter; the dropdown lists periods plus
"all". The alert page shows the prepared case, cited transactions and cited passages of the laws as today.

## Code layout
- `web/routes/world.py`, `web/routes/simulate.py`, `web/routes/evaluator.py`: routers; `web/jobs.py`: the single job.
- `simulation/resume.py`: replay, hash check, `advance` over a day list; `simulation/periods.py`: the `sim.*` repository.
- `web/frontend/` (Vite + React + D3): `Graph.tsx`, `Timeline.tsx`, `ClientList.tsx`, `ClientCard.tsx`, `SimulatePanel.tsx`.
- `review/repository.py` and the two SQL files `analyst_queue.sql`, `alerts.sql` get an optional period filter.
- `docs/adr/0026-local-ui.md` records this design; CLI `aml ui` starts the server and opens the browser.

The UI-run agent decides through `InvestigationService.investigate` with the profile switch on, so the investigation, its
citations and verification are stored exactly as the Analyst pages expect; `b4` stores only a row in `sim.decision`
(alert, recommendation) and the Analyst page says "rule B4, no prepared case".

## Checks
- Replay of N days then 7 more equals one uninterrupted run of N+7: same tape hash, same blocks.
- A killed job leaves a `failed` period; the next Simulate resumes without duplicate days or double spend.
- A second Simulate during a job is refused; the estimate and the guard refuse a run that passes the limit.
- `/api/world/*` responses contain no persona from `sim_actors`, group or scenario; `aml_app` still gets `permission denied`
  on `truth.sim_actors`; `/api/evaluator/groups` is absent without `--evaluator`.
- Changed persona counts show the threshold banner; unchanged counts do not.
- API tests on a small world with `b4` and the scripted system (no Azure). Frontend: type check and Vitest for the graph
  aggregation; one browser run by hand on the final build. `make check` stays green, with `ui` built in CI.

## Tasks
1. Split `DailyLoop` into `replay` and `advance`, with a hashing `Tape`; keep the M9 hash test green.
2. Migration `0007` and `simulation/periods.py`; resume with hash check and per-day transactions.
3. Job, estimate, cap and spend guard; `/api/simulate`, `/api/jobs/current`, `/api/estimate`, `/api/world`.
4. Read endpoints for graph, timeline, clients; display persona rule; evaluator router.
5. Analyst period filter and the B4 "no prepared case" page.
6. Frontend: Simulate panel and client list and card, then graph and timeline, then the polish pass.
7. ADR-0026, `aml ui`, `make ui`, tests and CI build.
