# ADR-0011: Synthetic payment-reference field

Status: accepted · 2026-10-28

## Context
Prompt-injection tests need attacker-controlled text inside the data. The dataset has no free-text field;
real bank transfers carry a payment reference written by the payer.

## Decision
- Add `core.transactions.reference`, generated from benign templates by an RNG seeded with `txn_id` only,
  independent of labels.
- Injected texts live in `core.reference_injections` and are overlaid only by an MCP server started with
  `REFERENCE_VARIANT=injected` for the paired injection evaluation.

## Consequences
- This is a documented modification of the dataset.
- A test checks that reference templates are statistically independent of `is_laundering`.
