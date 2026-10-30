# Test split log

Every computation that touched labels of the test window (2022-09-09 .. 2022-09-10), whether intended or not.
The plan allows at most two openings for evaluation (M8); anything else is recorded here with what was seen
and what was decided because of it.

| Date | Milestone | What was computed | Decisions taken from it |
|---|---|---|---|
| 2026-10-29 | M2 | HI-Small: alerts and positive alerts per split, including test (191 positive of 9,145). Unintended: the diagnostic query did not exclude test labels. | None. Thresholds were fixed on train before this; they were not changed. |
| 2026-10-29 | M2 | HI-Small and LI-Small: positive share by number of rules per alert and pattern-attempt coverage, both over all splits including test. Unintended, same cause. | None. |

From here on, diagnostics use train and validation labels only; `aml truth` writes labels for all alerts
because the evaluation needs them, but reports read them for train and val only until M8.
