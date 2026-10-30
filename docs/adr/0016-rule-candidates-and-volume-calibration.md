# ADR-0016: Rule candidates, volume-based calibration, one database per dataset

Status: accepted · 2026-10-29

## Context
M2 needs six rules on 5–7M transactions, thresholds that are not tuned on test data, and two independent
datasets whose account and transaction IDs overlap.

## Decision
- **Candidates, then alerts.** Each rule writes one scored row per (rule, account, day) into
  `core.rule_candidates`, above a low floor. `monitor.alerts.build_alerts` applies thresholds and suppression
  and creates alerts. Re-thresholding never re-runs a rule.
- **Calibration by alert volume (option A).** Thresholds are set on the train window so that rules produce an
  alert budget the analyst team could handle. Labels are not used to choose thresholds; they are used only to
  report above/below-the-line curves and to check that the test window holds enough positive alerts (~150) for
  a measurable evaluation. Thresholds are fixed after calibration and applied unchanged to validation and test.
  The budget is chosen after the curves are measured.
- **Suppression.** A rule hit on the same account within the previous `suppression_days` suppresses a new hit, so
  a multi-day episode alerts once.
- **Exchange rates from the dataset.** No external rates exist for this synthetic world; `eur_per_unit` is the
  median received/paid ratio of cross-currency transactions, walked from Euro.
- **One database per dataset** (`aml_hi_small`, `aml_li_small`), same schema, selected by `aml --database`.

## Consequences
- Rules stay realistically blunt; the agent faces real false positives.
- Option B (thresholds tuned for laundering hits on train labels) was rejected: stronger rules but labels
  shape the monitor, and the agent's measured value then depends on that tuning.
- Ground truth stores all typologies of an alert (`typologies text[]`), not one.
