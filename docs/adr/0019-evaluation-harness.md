# ADR-0019: Evaluation harness — frozen samples, weighted metrics, systems behind one protocol

Status: accepted · 2026-10-30

## Context
M3 builds the examiner before the agent exists, so that M7–M8 only plug the LLM in. Design §8 fixes the
metrics; this ADR fixes how samples, systems, operating points and the test window work.

## Decision

### Samples
- One frozen sample per (database, split): validation 200 alerts, test 400 alerts, stratified by
  `escalate_truth` to ~1/3 positive (all positives when fewer exist). Seeded draw.
- Alerts are identified by their natural key `(account_id, alert_date)`, not `alert_id`: pseudonyms are
  deterministic, identity values are not guaranteed across rebuilds.
- Stored as `eval/samples/<database>/<split>.json`: seed, keys, and population counts per stratum (needed to
  reweight to natural prevalence). Committed.

### Systems
- Every system implements `evaluation.systems.System`: `name` and `async decide(alert) -> SystemDecision`
  (`recommendation`, optional `risk_score`, `typologies`, `verification_status`, `cost_usd`, `latency_ms`).
- Systems receive runtime-role settings only (`RuntimeSettings`: the `aml_app` and `aml_mcp_reader` DSNs and the
  database name) and never the evaluator DSN — a system that tried to read labels would fail.
- Systems that train (B3) get their train-window labels from the harness: `evaluation.training` reads them
  through the evaluator role and `open_system(..., train_labels=...)` passes them in; the system joins them to
  features it computes through the app role.
- Systems in M3: B0 escalate everything; B1 rule score = sum over the alert's hits of score / threshold;
  B3 gradient boosting on alert features (SQL, no labels), trained on all train-window alerts; "scripted" =
  the current investigation graph via `InvestigationService` (exercises the agent path end to end).

### Operating points
- Score-based systems (B1, B3) pick their threshold on the validation sample: the highest close rate whose
  weighted false-close rate is ≤ 5%. That threshold is stored and applied unchanged to test.

### Metrics
- Computed on the sample with stratum weights (population share / sample share), so they reflect the natural
  prevalence; unweighted values are reported alongside.
- False-close rate (primary), escalation precision, close rate, recall for pattern-based and other
  laundering, typology hit rate (when a system emits typologies), cost and latency.
- 95% intervals by stratified bootstrap (2,000 resamples, fixed seed).

### Results and the test window
- Each run writes `eval/results/<database>/<split>/<system>.json`: git commit, sample and thresholds file
  hashes, operating point, metrics with intervals, and per-alert decisions for error analysis. Committed.
- `--split test` requires `--open-test --reason "<text>"`; the harness appends the opening to
  `docs/test-split-log.md` itself.

### Speed
- `truth.candidate_truth` stores once, per rule candidate, whether its window contains laundering; calibration
  curves and feasibility join it instead of recomputing (LI-Small feasibility took 3–5 minutes).

## Consequences
- Rebuilding a database reproduces the same samples and comparable results.
- Building the test sample uses test labels for stratification only; that is recorded in the test-split log.
