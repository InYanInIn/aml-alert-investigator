# ADR-0001: Agent architecture — hybrid graph with a bounded investigation loop

Status: accepted · 2026-10-28

## Context
Three options were compared: (A) a fixed-step graph, (B) a free ReAct planner over all tools, (C) a fixed
evidence pack followed by a bounded tool loop. Quality, cost, testability, injection surface and
explainability pull in different directions.

## Decision
Use C. Every alert gets the same deterministic evidence pack; the model may then make at most `k` tool calls
within a token budget; the decision is produced by one structured-output call and verified deterministically.

## Consequences
- "Information considered" (AMLR Art. 77(1)(b)) is reproducible for every alert.
- Cost per alert has a hard upper bound.
- `k = 0` is architecture A, so the value of agentic investigation is measured as an ablation, not asserted.
- Trajectories inside the loop are non-deterministic; tests cover the deterministic frame and the loop with a
  scripted fake model.
