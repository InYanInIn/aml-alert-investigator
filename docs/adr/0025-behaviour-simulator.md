# ADR-0025: Behaviour simulator

Status: accepted · 2026-11-08 · design in `docs/m9-simulator-design.md`

## Context
M8 measured decisions on AMLworld alerts, where HI-Small has 134 `Individual` entities out of about 166,000 and they
produce 20 of 16,991 alerts, and the frozen agent decided as the no-LLM rule B4 did on 399 of 400 alerts, because the
rule is written into its prompt. AMLworld has no real payment purposes and no customer profiles, so amounts and links
are all a system can use, and rules are strong on those. M8 could not measure whether groups are found, how fast, how
much money leaves first, or how ordinary people are treated, and it could not ask whether an LLM uses information a
rule does not.

## Decision
- **Own time-stepped simulator, seeded, one day at a time.** AMLSim and AMLGentex are batch generators; the loop needs
  a generator that stops every day and reacts to blocks. Customers are parameterised, with no LLM.
- **The point is the text.** Each payment carries a purpose and each customer an onboarding profile. Honest and
  laundering customers draw from the same pools; `load_config` refuses a laundering reference key no honest event uses.
  The signal is the fit between story and customer, and benign confounders, including honest customers who do not match
  their profile, keep a mismatch from being a label.
- **Against circularity.** `config/simulation/scenarios.yaml` and the text pools are written from public typologies by
  an isolated subagent that does not see `monitoring/`, the prompts or the M8 results, and they are committed before
  the first alert of any world. The same author family (Claude) wrote both sides, so this reduces the circularity
  without removing it.
- **Two agent variants.** `azure` is M8 as merged, prompt version `2f43327f402d`. `azure_profile` has the same model
  and tools, a rewritten "How to decide" in its own prompt file with its own version, and customer profiles through
  `get_account_profile` only when the MCP server is built with the profile on. B4 and B1 never read the profile.
- **Paired comparison.** Blocks make runs diverge, so B4 decides every alert of an agent run in the shadow, with no
  effect on blocks.
- **Worlds.** Each world is a separate `aml_sim_*` database; `aml sim run` refuses any other name. Thresholds are
  calibrated on the development world with `budget_per_day = 2` and reused. The `azure_profile` prompt is tuned on the
  development world only, at most two iterations of 30 investigations, and frozen before the held-out world opens once.
- **Thresholds per customer segment.** Alert thresholds are calibrated per segment (individual, small business,
  corporation) with `budget_per_day` split by monitored accounts, because one global threshold per rule gave 33 of 35
  alerts on corporations and none on a laundering group.
- **Limits.** At most 60 investigations per agent run; agent spending in M9 stops at $15. No learning adversary.
  B3 is left out.

## Consequences
- The result is a stress test on synthetic behaviour. It does not estimate accuracy on real bank data.
- The analyst is perfect and reacts in one day, which is optimistic; the QA sample of closed alerts (ADR-0017) is not
  modelled.
- Prevalence is an assumption (about 20 accounts of 850).
- Agent results are counts without intervals; only the free systems get percentile ranges, over 20 seeds.
- A rule with no train-window candidates is now switched off with a warning instead of raising, because a small world
  can lack candidates for a rule. The frozen M8 thresholds are not affected.
