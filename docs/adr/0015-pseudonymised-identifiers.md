# ADR-0015: Pseudonymised account and entity identifiers

Status: accepted · 2026-10-28

## Context
Profiling (`docs/data-notes.md`) shows that the hex value of the source account number predicts laundering
involvement about 4× between the lowest and highest decile, and the effect survives controlling for activity.
Entity names also carry sequence numbers. An LLM is unlikely to exploit hex magnitude, but a feature-based
baseline could, and an honest evaluation should not depend on that guess.

## Decision
- At ingest, replace `(bank_id, account_number)` with `account_id = 'A' + first 12 hex chars of
  sha256(f"{salt}|{bank_id}|{account_number}")`, with `bank_id` as an integer, and entity IDs the same way
  with prefix `E` (`sha256(f"{salt}|{entity_id}")`). 12 hex chars (48 bits), not 10: with ~520K accounts,
  40 bits gives about an 11% chance of a birthday collision, 48 bits under 0.1%.
- Keep only the entity type from the entity name, not its number.
- The salt is a fixed project constant: the goal is removing ordinal information, not secrecy.
- Ingest fails if two source keys map to the same pseudonym.
- The original identifiers are not loaded into `core`.

## Consequences
- IDs shown to the model and the analyst carry no ordering signal.
- Pseudonymisation is also what a bank would do before sending data to an external model.
