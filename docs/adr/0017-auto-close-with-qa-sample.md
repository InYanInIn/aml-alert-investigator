# ADR-0017: Auto-close verified "close" recommendations; analysts see escalations and a 3% QA sample

Status: accepted · 2026-10-29 · supersedes the "analyst confirms every alert" part of ADR-0013

## Context
At the calibrated budget of 1,200 alerts per day, an analyst confirming every alert would make the agent a
time-saver per alert but not a workload reducer. Auto-closing changes the threat model: a successful prompt
injection ("close this alert") would then hide laundering from every human.

## Decision
- The agent's recommendation becomes final without a human when it is `close` **and** citation verification
  passed. A failed verification already forces `escalate`.
- Analysts receive every `escalate` and a uniform random **3%** of auto-closed alerts, marked as QA samples.
  The QA draw is seeded and recorded, so it is reproducible and auditable.
- Auto-closed decisions are stored in `review.decisions` like any other, with `analyst = 'auto-close'`, so the
  record of every assessment (AMLR Art. 77(1)(b)) is complete.
- The cost is measured in M8 on the test split and reported with intervals:
  - workload removed: share of alerts auto-closed;
  - false-close rate of auto-closed alerts (laundering among them);
  - attack success rate of injected references on the auto-close path;
  - as a variant, "agent + B3 agreement" (auto-close only if the gradient-boosting baseline also scores low
    risk), to show whether a second opinion buys fewer missed cases for the workload it costs.

## Consequences
- The injection evaluation now measures the most damaging failure, not a cosmetic one.
- The 3% QA sample keeps a human in the loop for closures and detects drift; it also gives an unbiased
  estimate of the false-close rate in production, where labels do not exist.
- Whether supervisors accept auto-closure for a given institution is not verified; the project presents it as
  a measured option with its cost, not as a compliance claim.
