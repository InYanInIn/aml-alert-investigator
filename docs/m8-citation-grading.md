# Citation grading, M8 test run (agent C, system azure)

## Method
Population: latest row per alert_id in review.investigations inside the test windows (aml_hi_small 2026-11-07 18:09-19:37 UTC, 200 alerts: 170 escalate, 30 close; aml_li_small 21:49-23:09 UTC, 200 alerts: 160 escalate, 40 close). A citation is one (claim, passage_id) pair from the who/what/when/where/why/how claims. Sampling: Python random, seed 8; per dataset first the alerts (HI: 2 close + 13 escalate; LI: 3 close + 12 escalate, roughly proportional), then one random citation per alert, so alerts are distinct. Passage texts come from corpus.passages. Rubric: 2 supports (passage states the obligation, risk factor or definition the claim relies on), 1 related (same topic, does not back the specific statement), 0 does not support (unrelated, contradicts, or wrong article for the content). Graded by an LLM grader; one grader, no second opinion. Only the sampled passage was graded; other passages on the same claim were not. n=30 is small, so the mean has a wide margin.

## Table
| DS | alert_id | rec | field | claim (short) | passage_id | grade | reason |
|---|---|---|---|---|---|---|---|
| HI | 862 | escalate | why | Escalate: inbound payments then concentrated large outflows to one counterparty; exceed national reporting thresholds (art. 72). | PL-AML art. 72 ust. 1 | 2 | Euro 15,000 threshold reporting of transfers; amounts not shown in claim |
| HI | 1090 | escalate | why | Escalate: inbound plus same-day large outgoing transfers; report suspicions to FIU when grounds exist. | AMLR art. 69(1) | 2 | Reporting on knowledge/suspicion/reasonable grounds stated |
| HI | 2170 | escalate | why | Escalate: funds left and returned via same counterparty (cycle); reporting under art. 69(1)/(2). | AMLR art. 69(1) | 1 | Reporting duty only; passage does not say a cycle is a suspicion ground |
| HI | 3185 | escalate | why | Escalate: funds received then paid out; must report suspicions and monitor consistency with customer profile. | AMLR art. 26(1) | 2 | Ongoing monitoring against customer profile stated |
| HI | 3242 | escalate | why | Escalate: USD collected and forwarded same day; PL art. 43 ust. 4 requires investigation of unusual transactions. | PL-AML art. 43 ust. 4 | 2 | Duty to clarify complex/high-value/atypical transactions stated |
| HI | 3662 | close | why | Close: only outgoing payments, no inbound/self/conversion; assessment follows art. 69(2) (links, origin/destination). | AMLR art. 69(2) | 1 | Assessment factors only; does not back closing on spend-own-funds |
| HI | 4375 | escalate | why | Escalate: inbound payments, no other accounts, cheque over threshold; reporting duties apply under 69(1). | AMLR art. 69(1) | 1 | Generic reporting duty; claim is about closure conditions and monitoring |
| HI | 4527 | escalate | why | Escalate: large incoming ACH, same-day outbound flow; enhanced examination under art. 34(2). | AMLR art. 34(2) | 2 | Examine origin/destination of unusually large/unusual-pattern transactions |
| HI | 5146 | escalate | when | when: The three evidence txns occurred on 2022-09-10 at the recorded timestamps. | AMLR art. 26(1) | 0 | Ongoing-monitoring article cited for a pure date statement |
| HI | 6860 | close | why | Close: only outgoing payments, no self-payments or conversion; assessment guided by art. 69(2) and 34(2). | AMLR art. 69(2) | 1 | Assessment factors only; does not back closure |
| HI | 8805 | escalate | how | how: account collected MXN from many senders and forwarded MXN to payees same day; largest forwards listed. | AMLR art. 69(2) | 1 | Mentions links/origin of funds but does not back the described collect-and-forward mechanism |
| HI | 9945 | escalate | why | Escalate: large Euro outflows and onward movement; PL art. 72 ust. 1 addresses threshold reporting. | PL-AML art. 72 ust. 1 | 2 | Passage is the 15,000 EUR threshold reporting duty; claim says only that it addresses threshold reporting |
| HI | 11572 | escalate | what | what: inbound payments, larger payouts, recipients forwarded funds onward (txns listed). | AMLR art. 34(2) | 1 | Descriptive what-claim; passage not needed and does not state the flows |
| HI | 12629 | escalate | why | Escalate: large outbound ACH and inbound funds in same window; assess under art. 69(2) and PL art. 43 ust. 4. | PL-AML art. 43 ust. 4 | 1 | Large outbound plus inbound in one window is not shown to be atypical or unjustified |
| HI | 16418 | escalate | why | Escalate: outgoing then incoming from same counterparty (return path); linked transactions justify assessment under 69(2). | AMLR art. 69(2) | 2 | Link between transactions and origin/destination of funds named in passage |
| LI | 1029 | escalate | why | Recommend escalation: funds sent out and returned from same counterparty; reporting obligations apply (69(1), PL 35 ust. 1). | AMLR art. 69(1) | 1 | Unqualified reporting duty asserted from a return pattern; PL art. 35 ust. 1 cited alongside is a CDD trigger |
| LI | 1093 | escalate | why | Escalate: inbound USD gathered, large USD cash outflows forwarded; PL art. 43 ust. 4 and AMLR 69(2). | PL-AML art. 43 ust. 4 | 2 | Duty to clarify complex/high-value/atypical transactions stated |
| LI | 1570 | escalate | how | how: seventeen inbound Bitcoin payments collected, not forwarded; escalate and include in SAR. | AMLR art. 69(1) | 1 | Reporting duty cited for collected-but-unmoved funds; no suspicion ground in passage |
| LI | 1718 | escalate | why | Escalate: cash inflows from many senders, funds forwarded; examination of origin/destination (34(2)) and reporting (69(1)). | AMLR art. 34(2) | 2 | Unusual pattern / origin and destination of funds examination |
| LI | 1958 | escalate | why | Escalate: funds received and passed through, recipients forwarded onward; investigate atypical transactions (PL 43 ust. 4). | PL-AML art. 43 ust. 4 | 2 | Atypical or complex transactions must be clarified |
| LI | 3255 | close | how | how: ACH and card/cheque outflows; no inbound hops, no self-transfers, so no pass-through or conversion indicators. | AMLR art. 69(2) | 1 | Assessment factors only; descriptive how-claim about absence of layering |
| LI | 4724 | escalate | why | Escalate: inbound ACHs followed by immediate outbound; obliged entities must report suspicions (69(1)), assess pattern (69(2)). | AMLR art. 69(1) | 2 | General duty to report suspicions stated, claim states it as such |
| LI | 4931 | escalate | why | Escalate: funds cycled between two accounts within hours, substantial payout; PL art. 43 ust. 4 requires investigation. | PL-AML art. 43 ust. 4 | 2 | Duty to explain unusual high-value transactions stated |
| LI | 6188 | escalate | why | Escalate: rapid consolidation of many incoming payments; atypical transactions justify intensified scrutiny (34(2)). | AMLR art. 34(2) | 2 | Unusual pattern; origin and destination examination stated |
| LI | 6807 | escalate | what | what: concentrated fan-in of inbound ACH credits in USD to the alerted account. | AMLR art. 69(2) | 1 | Descriptive what-claim; passage is a suspicion-assessment rule, not backing fan-in description |
| LI | 6967 | escalate | why | Escalate: large outgoing tranches forwarded by recipients; assess link between transactions (69(2)). | AMLR art. 69(2) | 2 | Link between transactions assessed; reporting duty also in 69(1) |
| LI | 7163 | close | why | Close: only outgoing cheques, no layering; 69(2) suspicion based on links between transactions; structuring not supported. | AMLR art. 2(1)(20) | 1 | Linked-transactions definition; claim relies on 69(2) logic, not on the definition |
| LI | 7849 | close | why | Close: all four closing conditions met; under art. 26(1) obliged entities monitor and may deprioritise routine spending. | AMLR art. 69(1) | 0 | Suspicion-reporting article cited for monitoring and deprioritising; text itself names art. 26(1) (also cited); wrong article for the content |
| LI | 8535 | escalate | why | Escalate: outbound then inbound, aggregated large ACH inflows; reporting/clarification under 69(2) and PL art. 43 ust. 4. | PL-AML art. 43 ust. 4 | 2 | Unusual/complex high-value circular flow; clarify and investigate |
| LI | 10809 | escalate | why | Escalate: funds left and returned from same counterparty, no other accounts; escalation under 69(2) and PL 35 ust. 1. | AMLR art. 69(2) | 2 | Link between transactions and origin/destination named; PL art. 35 ust. 1 cited alongside is a CDD trigger, weak |

## Score distribution
2: 16, 1: 12, 0: 2. Mean 1.47 (HI 1.40, LI 1.53).

## Main failure patterns
1. Generic reporting/assessment article used as decoration (AMLR art. 69(1)/(2) is the default cite): it states the reporting duty or assessment factors but does not back the specific statement, especially on close decisions and on descriptive what/how claims (12 of the 30, all grade 1).
2. Citations attached to claims that make no regulatory statement (when, what, how): art. 26(1) on a pure date claim (HI alert 5146) and 69(2) on descriptive flows.
3. Wrong or mismatched article: AMLR art. 69(1) cited where the text argues monitoring/deprioritising (LI alert 7849; text names art. 26(1)); AMLR art. 2(1)(20) cited where the text argues 69(2) (LI alert 7163); PL-AML art. 35 ust. 1 (CDD trigger) cited as a basis for escalation or reporting (LI alerts 1029, 10809; HI alert 4375 in claim text).
Also: on close decisions the passages do not support closing at all, because no AML provision licenses closure on "spends own funds"; the agent borrows reporting rules.

## Recommended for human spot-check
1. HI alert 5146, when, grade 0 (Ongoing-monitoring article cited for a pure date statement). Claim: when: The three evidence txns occurred on 2022-09-10 at the recorded timestamps. Passage AMLR art. 26(1): "Obliged entities shall conduct ongoing monitoring of business relationships, including transactions undertaken by the customer throughout the course of a business relationship,..."
2. LI alert 7163, why, grade 1 (Linked-transactions definition; claim relies on 69(2) logic, not on the definition). Claim: Close: only outgoing cheques, no layering; 69(2) suspicion based on links between transactions; structuring not supported. Passage AMLR art. 2(1)(20): "‘linked transactions’ means two or more transactions with either identical or similar origin, destination and purpose, or other relevant characteristics, over a..."
3. LI alert 7849, why, grade 0 (Suspicion-reporting article cited for monitoring and deprioritising; text itself names art. 26(1) (also cited); wrong article for the content). Claim: Close: all four closing conditions met; under art. 26(1) obliged entities monitor and may deprioritise routine spending. Passage AMLR art. 69(1): "Obliged entities, and, where applicable, their directors and employees, shall cooperate fully with the FIU by promptly: (a) reporting to the FIU,..."
4. HI alert 2170, why, grade 1 (Reporting duty only; passage does not say a cycle is a suspicion ground). Claim: Escalate: funds left and returned via same counterparty (cycle); reporting under art. 69(1)/(2). Passage AMLR art. 69(1): "Obliged entities, and, where applicable, their directors and employees, shall cooperate fully with the FIU by promptly: (a) reporting to the FIU,..."
5. HI alert 3662, why, grade 1 (Assessment factors only; does not back closing on spend-own-funds). Claim: Close: only outgoing payments, no inbound/self/conversion; assessment follows art. 69(2) (links, origin/destination). Passage AMLR art. 69(2): "For the purposes of paragraph 1, obliged entities shall assess transactions or activities carried out by their customers on the basis of..."
6. HI alert 11572, what, grade 1 (Descriptive what-claim; passage not needed and does not state the flows). Claim: what: inbound payments, larger payouts, recipients forwarded funds onward (txns listed). Passage AMLR art. 34(2): "Obliged entities shall examine the origin and destination of funds involved in, and the purpose of, all transactions that fulfil at least..."
7. HI alert 862, why, grade 2 (Euro 15,000 threshold reporting of transfers; amounts not shown in claim). Claim: Escalate: inbound payments then concentrated large outflows to one counterparty; exceed national reporting thresholds (art. 72). Passage PL-AML art. 72 ust. 1: "Instytucje obowiązane, z wyjątkiem instytucji, o których mowa w art. 2 ust. 1 pkt 11, 13–15a i 18, przekazują Generalnemu Inspektorowi informacje..."
8. LI alert 1029, why, grade 1 (Unqualified reporting duty asserted from a return pattern; PL art. 35 ust. 1 cited alongside is a CDD trigger). Claim: Recommend escalation: funds sent out and returned from same counterparty; reporting obligations apply (69(1), PL 35 ust. 1). Passage AMLR art. 69(1): "Obliged entities, and, where applicable, their directors and employees, shall cooperate fully with the FIU by promptly: (a) reporting to the FIU,..."

## Human spot-check
The author graded the 8 rows above after the LLM grader had graded them, seeing its grade and reason. The rows are
a mix of grades chosen by the grader, not a random sample; the other 22 citations have the LLM grader's grade only.

| Row | Dataset, alert | Passage | LLM grader | Author |
|---|---|---|---|---|
| 1 | HI 5146 | AMLR art. 26(1) | 0 | 0 |
| 2 | LI 7163 | AMLR art. 2(1)(20) | 1 | 1 |
| 3 | LI 7849 | AMLR art. 69(1) | 0 | 0 |
| 4 | HI 2170 | AMLR art. 69(1) | 1 | 1 |
| 5 | HI 3662 | AMLR art. 69(2) | 1 | 2 |
| 6 | HI 11572 | AMLR art. 34(2) | 1 | 1 |
| 7 | HI 862 | PL-AML art. 72 ust. 1 | 2 | 2 |
| 8 | LI 1029 | AMLR art. 69(1) | 1 | 2 |

Agreement on 6 of 8. Both differences raise a 1 to a 2: the author raised 2 of the 5 rows the LLM grader had graded 1.
With the author's grades on these rows the distribution over all 30 is 18 / 10 / 2 (grades 2 / 1 / 0), mean
1.53, against 16 / 12 / 2 and 1.47 for the LLM grader alone.
