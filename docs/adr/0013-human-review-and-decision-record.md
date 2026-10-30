# ADR-0013: Human confirmation and an append-only decision record

Status: accepted · 2026-10-28 · partly superseded by ADR-0017 (auto-close of verified closures)

## Context
AMLR Art. 77(1)(b) requires a record of every assessment, including those without an STR; Art. 77(2) allows
references only to information that cannot be modified. Art. 76(5) requires meaningful human intervention for
relationship and due-diligence decisions; closing an alert is not explicitly listed there, so the project does
not claim legal necessity, only alignment.

## Decision
- The agent proposes; the analyst confirms or overrides with a comment. Nothing is final without that step.
- `review.investigations` and `review.decisions` are insert-only for the app role, with an update-rejecting
  trigger; citations reference immutable transaction IDs and hashed passages.
- The LangGraph `interrupt()` sits alone in `human_review`; side effects happen in separate nodes because the
  interrupted node re-runs on resume.

## Consequences
- Overrides are data: the report includes the analyst-override rate during the demo.
