# M9 — Behaviour simulator: design

Status: approved · 2026-11-08

## Goal
Run the system in a small simulated bank, one day at a time, and measure what M8 could not: how many
laundering groups it finds, how fast, how much money leaves first, and how it treats ordinary people.

M8 measured decisions on alerts from AMLworld, where HI-Small has 134 `Individual` entities out of about
166,000 and they produce 20 of 16,991 alerts. The close policy needs at least three other accounts per
customer, which ordinary people rarely have. The simulator tests the system on that population.

M8 also showed that the frozen agent decides as the no-LLM rule B4 does (399 of 400), because the rule is written into
its prompt. So M9 answers a second question: **given information a rule does not use, does an LLM make better decisions
than the rule?** Two agent variants run on the same world for this.

**The point of the simulator is the text AMLworld lacks:** real payment purposes and customer profiles. There the amounts
and links are all a dataset offers, and rules are strong on those. Everything below serves making that text informative
without making it a label.

The result is a stress test on synthetic behaviour. It does not estimate accuracy on real bank data.

## Prior work
AMLworld's generator is distributed as a static dataset. AMLSim and AMLGentex (both Apache-2.0) are batch
generators without a detect-and-react loop. TransXion adds persona profiles as a static benchmark. Truman
drives customers with an LLM and reached 50 customers. A closed loop with an LLM investigator was not found in
this search, which read excerpts rather than full papers.

## Decisions
| Question | Decision | Reason |
|---|---|---|
| Generator | Own time-stepped Python simulator with a fixed seed | The loop needs a generator that stops every day and reacts to blocks |
| Customers | Parameterised behaviour, no LLM | Thousands of customer-days per world; reproducible; free |
| Monitoring cadence | Nightly for the agent; 1, 3 and 7 days compared on free systems | Rules are daily and look backward, so cadence changes delay rather than the set of alerts; cost is per alert |
| Investigations | At most 60 per agent variant, about 2 alerts a day | Far below the 200 per dataset in M8 |
| Systems under test | `azure`: M8 as merged (prompt v3, `gpt-5-mini` high, `k = 8`). `azure_profile`: same model and tools, judgement prompt, customer profile | The first shows whether the frozen system holds up; the second tests what the agent is for |

## World
About 850 monitored accounts at one bank. External accounts (employers, landlords, merchants, sources and sinks
of dirty money) exist so payments have counterparties but are never monitored.

| Persona | Accounts | `entity_type` | Accounts per customer | Behaviour |
|---|---|---|---|---|
| Student | ~200 | Individual | 1–2 | Stipend, money from parents, small card spending, tuition |
| Worker | ~200 | Individual | 1–2 | Monthly wage, rent, bills, cash, groceries |
| Office employee | ~200 | Individual | 1–3 | Monthly salary, rent or mortgage, card spending, savings |
| Business owner | ~150 | Sole Proprietorship, Partnership | 2–4 | Revenue, suppliers, tax, owner's drawings |
| Corporation | ~80 | Corporation | 3–6 | Many customers and suppliers, payroll, intra-group transfers |
| Laundering group member | ~20 | as the persona it hides among | as that persona | Follows a scenario |

Personas, amounts and timing live in `config/simulation/personas.yaml`. Each day an account draws monthly events
(wage, rent, tax), Poisson-distributed spending with log-normal amounts, and peer payments. Spending stops at the
account balance. Payment formats and currency names are those of AMLworld; most payments are in Euro.

Each payment carries a reference stating its purpose ("Rent March", "Tuition", "Invoice 4471 for pallets"). Launderers
write plausible cover stories from the same vocabulary the whole world uses. No word marks a payment as laundering; the
signal is the fit between story and customer: "consulting invoice" is normal for a consultancy and strange for a student.

**Benign confounders** are part of normal behaviour, otherwise only launderers would look unusual: roommates splitting
rent, a parent's transfer paid straight on as tuition, transfers between a customer's own accounts, currency exchange
before travel, one-off large purchases, seasonal revenue spikes, and honest customers whose activity does not match their
profile (a student selling a car or receiving an inheritance, an office employee with a side business).

**Customer profile.** Every customer has what a bank collects at onboarding, as text and numbers: occupation or line of
business, a short business description, the stated purpose of the account, expected activity, declared monthly income or
turnover, and the date the relationship began. Companies have a display name, so a counterparty can be looked up too. A
group member has the profile of the persona it hides among, so a mule is a student on paper and a shell company has an
ordinary-sounding description.

Texts are drawn from pools in `config/simulation/` written once by the same blind subagent, so a run needs no LLM to
generate them. Pools for honest and laundering customers are shared.

## Laundering scenarios
Six to eight groups run one scenario each, covering: student money mules, structuring below thresholds, a shell company
as pass-through, a funnel account, a cycle through several accounts, payments disguised as supplier invoices, and slow
layering over several weeks. About 20 accounts in total, which is an assumption: real prevalence is unknown.

Scenarios live in `config/simulation/scenarios.yaml` as steps (`from`, `to`, amount or share, count, delay, format,
reference). A small interpreter executes them; there is no code per scenario.

**Against circularity.** A subagent writes this file from public typologies, seeing only the transaction schema and
the persona list, and not `monitoring/`, the prompts or the M8 results. It is committed before the first alert of any
world. It also sets how often honest customers do not match their profile, so a mismatch is not a label in disguise.
Scenarios the rules probably cannot see stay in, and zeros are reported. The same LLM family
wrote both sides, so this reduces the circularity without removing it.

**No learning adversary.** A blocked account is replaced by a reserve member after two days; a group stops when it has
none left. There is no optimiser against the detector: it would add misuse risk to a public repository and is not needed
for the question.

## Daily loop
Simulated time stops while the pipeline works.

1. Generate the day for all accounts that are not blocked; insert accounts, transactions and truth.
2. On a monitoring day: regenerate rule candidates, delete those of external accounts, build alerts for the days since
   the last run.
3. The system under test decides each new alert, through `open_system` and `decide_all` as in M8.
4. Simulated analyst: an `escalate` on an account of a laundering group blocks it from the next day. Other escalations
   only count as workload. `close` does nothing.

Timeline: 21 days of history, then 28 live days (4 weekly rounds in the report). Only live days produce alerts.

The analyst is perfect and reacts in one day, which is optimistic. The QA sample of closed alerts (ADR-0017) is not
modelled. With a cadence above one day the agent still sees only data up to each alert's `as_of`.

## The two agent variants
| | `azure` | `azure_profile` |
|---|---|---|
| Prompt | v3 unchanged, contains the four-condition close rule | "How to decide" rewritten: weigh the activity against the customer's profile; no close rule, no examples from AMLworld |
| Payment purposes (`reference_untrusted`) | visible, as in M8 | visible, and the prompt asks to compare them with the profile |
| Customer profile in tools, also for counterparties | no | yes |
| Model, tools, `k`, citation rules, verifier | same | same |

The profile reaches the agent through `get_account_profile` only when the MCP server is started with the profile switched
on, the mechanism `REFERENCE_VARIANT` already uses, so `azure` sees exactly what it saw in M8. B4 and B1 never read it.

Payment purposes and profile text stay untrusted evidence: a customer can write anything, so the prompt treats them as
claims to compare with the money flows, and the rule against following instructions in them is unchanged.

The new prompt is tuned on the development world only, in at most two iterations of 30 investigations, then frozen before
the held-out world opens.

**Paired comparison.** Blocks differ between runs, so the transactions diverge after the first block. To compare decisions
on identical alerts, B4 runs in the shadow of every agent run: it decides each alert for free and has no effect on blocks.

## Worlds and runs
Each world is a separate database named `aml_sim_*`; `aml sim run` refuses any other name and resets the world tables.
Thresholds are calibrated per customer segment (individual, small business, corporation), with the budget split by
monitored accounts: global thresholds gave 33 of 35 alerts on corporations and none on a laundering group.

| Run | Seeds | System | Cost |
|---|---|---|---|
| Development world: debug, calibrate thresholds with `budget_per_day = 2` | one | B0, B1, B4, scripted | $0 |
| Development world: tune the `azure_profile` prompt | same | `azure_profile`, at most 60 investigations | ~$4 |
| Held-out world, opened once | one | `azure` and `azure_profile`, cadence 1 | ~$8 |
| Held-out world | same | B0, B1, B4, cadence 1 | $0 |
| Spread and cadence | 20 others | B4 and B1, cadence 1, 3, 7 | $0 |

Agent spending in M9 stops at $15. A held-out agent run stops at 60 investigations and marks the result as truncated. It reuses the development world's
thresholds. The corpus is copied from `aml_hi_small`, so no embedding calls are made. A held-out result is not used
to change the prompt, rules or scenarios.

**B3 is left out.** It trains on labelled alerts, and two alerts a day give too few for training. Reusing the HI-Small
model would measure distribution shift instead.

## Metrics
Per system, with one JSON file per run in `eval/results/aml_sim_*/sim/`:
- Groups found (at least one member blocked), overall and per scenario, zeros included.
- Days from a group's first laundering payment to its first block.
- Euro reaching the sink before the group stopped, against the amount planned.
- Alerts, close rate, false-close rate, escalation precision, reported per persona.
- Analyst workload per weekly round; cost and latency per alert.
- For each agent, against its shadow B4: alerts where they differ, split into laundering caught that B4 closed, honest
  customers closed that B4 escalated, and the reverse of both.

Each agent's run has about 50 alerts and 6–8 groups, so its results are reported as counts without intervals. The
free systems get percentile ranges over the 20 seeds.

## Code
- `simulation/` package: `world.py` (population), `behaviour.py` (a normal day), `laundering.py` (scenario interpreter),
  `loop.py` (daily loop), `results.py`. CLI `aml sim run --seed N --system S --cadence D` and `aml sim report`.
- Migration `0006`: `truth.sim_actors (account_id, persona, group_id, scenario)`, readable by `aml_evaluator` only;
  `core.customer_profiles (entity_id, display_name, occupation, business_description, account_purpose, expected_activity,
  declared_monthly_eur, customer_since)`.
- `azure_profile` in `open_system`; its "How to decide" text in a separate prompt file with its own prompt version, so the hash of
  the `azure` prompts stays the same.
- `monitoring/alerts.py`: alert building for a day range on a non-empty table. `first_days_parameters` already takes
  a `DayRange`, and suppression already looks back before it.
- `monitoring/calibration.py`: `choose_thresholds` warns and switches a rule off instead of raising when the rule has no
  train-window candidates, so a small world can lack a rule.
- `txn_id` follows `(ts, sha256(seed, row))` within a day, and account ids are pseudonymised, so identifiers do not
  reveal labels (ADR-0002, ADR-0015).
- `docs/results.md`: a generated M9 section with its own markers. ADR-0025 records this design.

## Checks
- The same seed and system produce an identical transaction hash.
- Candidates for days up to D are the same whether the tape ends at D or continues (no look-ahead).
- `aml_mcp_reader` and `aml_app` get `permission denied` on `truth.sim_actors`; no MCP response contains a persona or
  group.
- With the profile switched off, MCP responses contain no profile field, and `azure` runs with an unchanged prompt version.
- External accounts never get alerts; blocked accounts have no transactions after the block.
- A small world runs end to end in CI with B4 and the scripted system.
- `docs/results.md` M9 is generated from committed JSON, including every scenario with zero detection.

## Out of scope
LLM-driven customers, a learning adversary, new rules, changes to the frozen `azure` prompt, B3, streaming, several banks,
UI.
