# ADR-0010: Baselines include a gradient-boosting classifier

Status: accepted · 2026-10-28

## Context
A rules-only or single-prompt baseline is easy to beat. An interviewer will ask why not train a classifier.

## Decision
Compare against B0 (escalate all), B1 (rule score), B2 (single prompt, no tools) and B3 (scikit-learn
gradient boosting on alert features, trained on the train window), plus the `k = 0` ablation.

## Consequences
- B3 may beat the agent on the primary metric. If so, the report says so; the agent's remaining value is the
  verified investigation record and the handling of borderline cases, which the report then has to show.
