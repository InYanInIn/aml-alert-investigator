# ADR-0003: Ground truth at alert level; typology scored only on pattern alerts

Status: accepted · 2026-10-28 · deviates from the brief

## Context
The dataset labels transactions, the agent decides about alerts. A laundering label on one transaction does
not by itself make an alert worth escalating. The brief listed structuring as a typology, but structuring is
not among the dataset's 8 patterns.

## Decision
- `escalate_truth` = any laundering transaction of the alerted account inside the alert window.
- `escalate_truth_account_level` = account ever involved; reported as a sensitivity check.
- `typology_truth` = pattern type when the alert's laundering transactions intersect a pattern attempt, else
  `none`. Typology accuracy is computed only where it is not `none`.
- Structuring (R5) stays as a rule; its typology is not scored.
- Recall is reported separately for pattern-based and other laundering.

## Consequences
- The ground-truth definition is a modelling choice; reporting both variants makes it inspectable.
- Typology accuracy is measured on a subset; its sample size is stated next to the number.
