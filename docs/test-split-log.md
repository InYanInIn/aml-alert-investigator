# Test split log

Every computation that touched labels of the test window (2022-09-09 .. 2022-09-10), whether intended or not.
The plan allows at most two openings for evaluation (M8); anything else is recorded here with what was seen
and what was decided because of it.

| Date | Milestone | What was computed | Decisions taken from it |
|---|---|---|---|
| 2026-10-29 | M2 | HI-Small: alerts and positive alerts per split, including test (191 positive of 9,145). Unintended: the diagnostic query did not exclude test labels. | None. Thresholds were fixed on train before this; they were not changed. |
| 2026-10-29 | M2 | HI-Small and LI-Small: positive share by number of rules per alert and pattern-attempt coverage, both over all splits including test. Unintended, same cause. | None. |
| 2026-11-07 | M3 | aml_hi_small: test sample drawn; strata counts used for stratification: 191 positive and 8954 negative alerts. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M3 | aml_li_small: test sample drawn; strata counts used for stratification: 134 positive and 2396 negative alerts. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M3 | aml_hi_small: b0 evaluated on the test sample (200 alerts); metrics with intervals. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M3 | aml_hi_small: b1 evaluated on the test sample (200 alerts); metrics with intervals. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M3 | aml_hi_small: b3 evaluated on the test sample (200 alerts); metrics with intervals. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M3 | aml_hi_small: b3_no_age evaluated on the test sample (200 alerts); metrics with intervals. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M3 | aml_hi_small: b4 evaluated on the test sample (200 alerts); metrics with intervals. Reason: M8 final evaluation: config frozen after val (prompt v3 2f5f830, gpt-5-mini high, k=8) | None. |
| 2026-11-07 | M8 | aml_hi_small: azure (C) run on the test sample hung at 198 of 200 alerts on two Azure requests without a timeout; killed after seven hours, no results written. The whole chain is rerun after adding a 600-second request timeout. | Request timeout added to the Azure client; no change to prompt, model or decision config. |

From here on, diagnostics use train and validation labels only; `aml truth` writes labels for all alerts
because the evaluation needs them, but reports read them for train and val only until M8.
