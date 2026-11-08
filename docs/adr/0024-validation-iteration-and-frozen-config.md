# ADR-0024: Validation iteration and the configuration frozen for test

Status: accepted · 2026-11-07 · amends ADR-0023

## Context
M8 iterated on the 200 HI-Small validation alerts before opening test once. What happened:
- **Prompt v0** (M7 prompt, verifier fixes, reasoning `medium`): C escalated 197 of 200 alerts (weighted
  close rate 0.015); A and B2 closed none. The rationales cited the rule hit itself as the reason to escalate.
- **Prompt v1** ("the rule hit is not by itself a reason to escalate"): on the first 50 val alerts, close rate
  0.119 at false-close rate 0.056 (B3 on the same alerts: 0.114 at 0.000).
- A read-only analysis of the train split (alert dates 1–6 September) found what separates laundering from
  noise in what the tools show: money passing through the account, above all incoming funds converted by a
  payment to the account itself and paid out at once (positive rate 50% vs 6%); no incoming payment in the
  window (3% vs 10%); the customer holding several other accounts (lower rate). Only ACH payments carried
  laundering; wire and reinvestment payments carried none.
- **Prompt v2** (those signals, two train examples, reasoning `high`): first 50 val alerts closed 20 of 32
  benign but 5 of 18 laundering alerts, mostly fan-in alerts with many inflows and nothing paid out yet.
- **Prompt v3** (close only when all four own-money conditions hold): first 50 val alerts closed 13 of 32
  benign and none of 18 laundering alerts.
- `gpt-5-mini` accepts reasoning effort `xhigh` but spends no reasoning tokens with it; `high` is the real
  maximum. Newer models (gpt-5.4-mini, gpt-6-luna and others) have no quota on the Free Trial subscription;
  creating a gpt-6-luna deployment failed with `InsufficientQuota`.

## Decision
- **Frozen for test:** prompt v3 (commit 2f5f830), `gpt-5-mini` 2025-08-07, reasoning effort `high`,
  `k = 8` follow-up tool calls, the retrieval and verifier of ADR-0023 and M8.
- **B4.** A no-LLM baseline that applies the prompt's close policy directly: close only if the account
  received no payment in the alert window, made no payment to itself, no payment changed currency, and the
  customer holds at least three other accounts. Without it the comparison with B3 would credit the model
  with what the rule does.
- **Test:** 200 alerts per dataset (HI-Small and LI-Small), C, A, B0–B4 and B2, injection pairs and the
  clean-vs-clean control on C; spending stops at $95 for the test chain.

## Consequences
- On val, C and B4 take the same decision on all 200 alerts (close rate 0.258, false-close rate 0.060;
  B3 0.147 at 0.045; B3 closing as many benign alerts as B4 lets 7 laundering alerts through instead of 4).
  The model's contribution to the decision is applying the policy; what it adds over B4 is the investigated,
  cited case for the analyst (197 of 200 decisions passed citation verification).
- The close policy was found on train and checked on val, which also chose between prompt versions; only
  test measures it without that selection.
- `high` costs about $0.063 per C investigation (v0 at `medium`: $0.029).
