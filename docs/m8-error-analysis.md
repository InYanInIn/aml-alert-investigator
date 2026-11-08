# M8 test: error analysis of agent C (azure, gpt-5-mini), 20 wrong decisions

**Method.** Sources: `eval/results/aml_{hi,li}_small/test/{azure,b3,b4}.json` (decisions, truth, verification status); Postgres `review.investigations` (latest row per alert inside the test-run time window, for the agent's written reasoning), `core.*` (window transactions, rule hits, accounts per entity) and `truth.*` (alert and transaction labels), read-only. Selection: all 5 LI false closes (5305, 10715, 8390, 2550, 2936); the one C/B4 disagreement (LI 7947); every non-passed alert that is also wrong (LI 6353, 7947; HI 14408, LI 182 and LI 9153 have status failed but are correct escalations, so excluded); the remaining 13 are false escalations drawn with `random.Random(20261107)`: 7 from the 103 HI false escalations, 6 from the 96 remaining LI ones (sorted by alert_id before sampling). Window figures are EUR-equivalent (`amount_paid_eur`). B3 is the gradient-boosted classifier; B4 is the no-LLM close rule. Statements marked (inference) are mine, not read from data.

## The 20 cases

Truth: F = benign (should close), L = laundering (should escalate). C/B3/B4: C = close, E = escalate. Window = alert window, in/out = payments to/from the account.

| DS | alert | truth | C | B3 | B4 | what the data shows / what C wrote | cause |
|---|---|---|---|---|---|---|---|
| LI | 5305 | L | C | E | C | Window: one outgoing ACH, EUR 8.05M, labelled laundering; entity has 7 other accounts. C cited all four close conditions, correctly applied | A |
| LI | 10715 | L | C | E | C | One outgoing ACH, about 15.8k, labelled laundering (truth typology bipartite); 7 other accounts. C applied the rule correctly | A |
| LI | 8390 | L | C | E | C | One outgoing ACH, about 18.0k, labelled laundering; 3 other accounts (exactly the minimum). C applied the rule correctly | A |
| LI | 2550 | L | C | E | C | Two outgoing ACH, about 13.8k together, both labelled laundering (bipartite); 32 other accounts | A |
| LI | 2936 | L | C | E | C | Outgoing ACH about 13.3k (labelled laundering) plus a small cheque and card payment; 3 other accounts (gather_scatter) | A |
| LI | 7947 | F | E* | E | C | Window: outgoing ACH about 1.8k and cheque about 0.3k, nothing in; 4 other accounts. Written decision says close and is right, but verification failed (a number in the "why" claim not backed by a fact or cited id) and routing escalated it | B |
| LI | 6353 | F | E | E | E | In: two cheques about 2.2k; out: ACH, cheque and card about 4.7k; 13 other accounts. C wrote pass-through with onward forwarding; label benign. Verification failed (number 26 in the claim text) | C |
| HI | 12048 | F | E | E | E | In: cheque and card about 0.2k; out about 1.5k incl. one ACH. C wrote rapid pass-through; no in-window inflow is large enough to pass through | C |
| HI | 14256 | F | E | E | E | In: cheque 13.4k, card 1.4k; out: cheques and cards 1.8k. C escalated only because an inbound payment exists, citing the close condition | C |
| HI | 7073 | F | E | E | E | In: ACH, cash, cheque about 0.9k; out about 5.6k incl. 2 ACH. C wrote same-day onward movement | C |
| HI | 8805 | F | E | E | E | In: cheques and cards about 3.0k (Mexican peso); out about 19k in cheque and card. C wrote pass-through, noting the account received inbound funds | C |
| HI | 862 | F | E | C | E | In about 37k; out about 1.4M (ACH 218k, cheques 885k, wire 288k). C wrote concentrated outflows after inbound. B3 correctly closed | C |
| HI | 3231 | F | E | E | E | In: one small ACH (184); out about 2.7M (ACH 1.47M, cheque 1.18M). C cited the in-window ACH as failing the close condition | C |
| HI | 15868 | F | E | C | E | In about 0.6k; out about 63.6k, mostly one cheque of 63.3k. C escalated on the inbound plus the large cheque. B3 correctly closed | C |
| LI | 1072 | F | E | E | E | In: ACH, cash, cheque, card about 0.4k; out about 2.9k. C cited "no payments received" not met and a backward trace hop | C |
| LI | 8575 | F | E | E | E | Rule R3 pass-through; in about 238k (cash, cheque, card, wire), out about 202k to one account. C: supported bipartite and stack; truth label benign | D |
| LI | 4970 | F | E | E | E | Rule R4 cycle; in 3 ACH about 4.6k, out about 18.9k; entity has 1 other account. C found a partial return cycle (4918059 to 6077716); label benign | D |
| LI | 3857 | F | E | E | E | Rule R4 cycle; in ACH 1.1k, out ACH 1.8k; C found a short return flow (4739135, 6712778); label benign | D |
| LI | 9993 | F | E | E | E | Rule R1 fan-in: 18 incoming ACH (shekel), about 46k; entity has 2871 other accounts. C marked fan_in supported; label benign | D |
| LI | 4482 | F | E | E | E | Window: single outgoing ACH about 4.0k, nothing in; entity has only 2 other accounts. C escalated because "close conditions are not all satisfied" | E |

\* C's written decision was close; the recorded recommendation is escalate after the verification failure.

## Categories

- **A. Policy false close.** Laundering that looks like spending own money: only outgoing payments, no conversion, 3+ other accounts. B4 makes the same error; C executed the rule correctly.
- **B. Execution: correct close overridden by citation check.** The written decision was right; verification failed and the alert was escalated. B4 was right.
- **C. Policy escalates on any incoming payment.** Benign alert, window has an inbound payment (often small cheque/card/cash); the rule forbids closing. B4 makes the same error. In 5 of the 9 (6353, 12048, 7073, 8805, 862) C's text describes pass-through or onward movement; in the others it cites only the inbound payment.
- **D. Structural rule hit with benign label.** R1/R3/R4 evidence (fan-in, pass-through, cycle) that C and B4 both escalate; the label says benign.
- **E. Policy escalates: too few other accounts.** Out-only window, but fewer than three other accounts. B4 makes the same error.

| category | count | policy error (B4 same) | execution error |
|---|---|---|---|
| A policy false close | 5 | 5 | 0 |
| B correct close lost to verification | 1 | 0 | 1 |
| C incoming payment forces escalate | 9 | 9 | 0 (narrative overstated in some) |
| D structural hit, benign label | 4 | 4 | 0 |
| E too few other accounts | 1 | 1 | 0 |
| total | 20 | 19 | 1 |

## Findings

1. **Nearly every error is the policy's, not the model's.** In 19 of 20 cases B4 reaches the same decision as C. All 5 LI false closes are laundering transactions in alerts with a single outgoing ACH, no inflow and 3 or more other accounts; the rule cannot see them, and C followed it exactly. B3 escalated all five.
2. **The close rule is blunt in both directions.** Any inbound payment in the window forces an escalation, including small cheque or card receipts next to far larger outflows (HI 14256: 14.8k in, 1.8k out; HI 12048). Fewer than three other accounts also forces escalation (LI 4482). These produce the false escalations the sample shows; B3 closed two of the HI ones correctly (862, 15868).
3. **The model's narratives sometimes overstate.** In several category C cases C wrote "pass-through" or "layering" where inflow was a small share of outflow or of a different kind (HI 12048, 8805). The decision was still forced by the policy, so this affects the analyst's reading of the explanation, not the outcome. Whether it matters for review is a judgement call (inference).
4. **One model-side loss is a citation check, not reasoning.** LI 7947 was correctly closed in the written decision and escalated because a number in the claim text had no backing fact. Of the 5 non-passed alerts (HI 1, LI 4), only LI 7947 and 6353 are wrong; the other three are correct escalations.
5. **Some "benign" labels sit on structurally suspicious alerts (inference).** In category D the data shows fan-in (18 ACH in), cycles and a pass-through hit, while the label is benign; I cannot tell from the data whether these are label noise or synthetic-data quirks, and the sample is four alerts.
