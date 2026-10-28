# ADR-0002: Dataset — IBM AMLworld, HI-Small for development, LI-Small also reported

Status: accepted · 2026-10-28 · deviates from the brief

## Context
IBM "Transactions for Anti Money Laundering" (Altman et al., NeurIPS 2023), Kaggle version 8, licence
CDLA-Sharing-1.0. Verified facts: six independent variants; HI-Small ≈ 5M transactions over 10 days;
`*_Patterns.txt` lists transactions of 8 AMLSim patterns; labels propagate through commingled funds, so most
laundering is outside the patterns (LI-Large: 19.5K of 100.6K). The dataset's own best models score
63% F1 on HI-Small and 27% on LI-Small. Kaggle says transactions after the primary date range are all
laundering; measured in M0, 59–60% are (`docs/data-notes.md`).

The brief assumed the patterns file describes the laundering; it describes a minority of it.

## Decision
- Develop on HI-Small; report test results on HI-Small and LI-Small.
- Drop transactions after the primary date range at ingest.
- Assign `txn_id` by `(ts, sha256(row), occurrence index)`, not file order: file position predicts the label
  and exact duplicate rows exist (M0).
- Revisit HI-Medium (16 days) only if 10 days of history make profile features useless (decided after M2).

## Consequences
- LI-Small is the harder and more honest test; results there are expected to be lower.
- Synthetic data limits external validity; the README states this explicitly.
- File formats and counts are recorded in `docs/data-notes.md` (M0); account identifiers are pseudonymised
  (ADR-0015).
