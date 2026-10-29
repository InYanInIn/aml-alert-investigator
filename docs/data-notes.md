# Data notes

Measured on 2026-10-28 from Kaggle dataset `ealtman2019/ibm-transactions-for-anti-money-laundering-aml`,
version 8 (files dated 2025-07-08). Reproduce with:

```
uv run scripts/profile_raw_data.py data/raw HI-Small
uv run scripts/profile_raw_data.py data/raw LI-Small
```

## File formats

- `*_Trans.csv`: header `Timestamp, From Bank, Account, To Bank, Account, Amount Received, Receiving Currency,
  Amount Paid, Payment Currency, Payment Format, Is Laundering`. Timestamps are `YYYY/MM/DD HH:MM`, minute
  resolution. Bank IDs carry leading zeros (`010`, `03208`) and must be read as strings first.
- `*_accounts.csv`: `Bank Name, Bank ID, Account Number, Entity ID, Entity Name`. `Bank ID` matches the
  transaction bank as an integer (0 of 515,088 match as strings, all match as integers). `Entity Name` is
  `<entity type> #<number>`.
- `*_Patterns.txt`: blocks `BEGIN LAUNDERING ATTEMPT - <TYPE>[: <description>]` … `END LAUNDERING ATTEMPT -
  <TYPE>`. BIPARTITE, SCATTER-GATHER and STACK headers have no description. Rows inside use the transaction CSV
  format without a header; every pattern row matches exactly one transaction row.

## Headline numbers

| | HI-Small | LI-Small |
|---|---|---|
| Transactions | 5,078,345 | 6,924,049 |
| Laundering transactions | 5,177 (1 per 981) | 3,565 (1 per 1,942) |
| Timestamp range | 2022-09-01 .. 2022-09-18 | 2022-09-01 .. 2022-09-17 |
| Rows after 2022-09-10 | 1,108, of which 655 laundering (59%) | 223, of which 134 laundering (60%) |
| Pattern attempts / rows | 370 / 3,209 | 117 / 1,023 |
| Laundering covered by patterns | 62.0% | 28.7% |
| Self-transfers (same bank and account) | 591,212 | 804,477 |
| Cross-currency transactions | 72,170 | 98,876 |
| Exact duplicate rows | 9 | 8 |
| Accounts in transactions | 515,088 | 705,907 |
| Entities | 166,207 | 224,931 |

Discrepancies with published figures:
- HI-Small laundering: Kaggle says 5.1K (matches); the paper's Table 4 says 3.6K (does not match).
- LI-Small laundering: Kaggle and the paper say 4.0K; measured 3,565.
- Kaggle says transactions after the primary period are all laundering; measured 59–60%. All laundering rows in
  the tail are pattern rows (655 of 655 and 134 of 134): the tail is where pattern attempts finish.

HI-Small pattern attempts by type: BIPARTITE 49, CYCLE 54, FAN-IN 40, FAN-OUT 48, GATHER-SCATTER 51, RANDOM 41,
SCATTER-GATHER 44, STACK 43.

## Findings that change the design

1. **Account numbers leak the label.** Account involvement in laundering falls from 2.4% in the lowest decile of
   hex account numbers to 0.6% in the highest (HI-Small; LI-Small 2.0% → 0.6%). Controlling for activity does
   not remove it: among accounts with 4–13 transactions the rates are 3.9% (lowest decile) vs 1.1% (highest).
   → Pseudonymise account and entity identifiers at ingest (ADR-0015).
2. **File order leaks the label.** The file is not sorted by time, and the laundering rate varies about 4× across
   deciles of file position (HI-Small 0.039% → 0.155%). → `txn_id` must not follow file order (ADR-0002).
3. **Exact duplicate rows exist** (9 and 8). → `(ts, sha256(row))` alone does not give unique IDs; the
   occurrence index within identical rows is the final tie-break.
4. **The tail is 60% laundering.** → Drop rows after 2022-09-10 at ingest, as planned; pattern attempts that
   finish in the tail are truncated, and ground truth uses only their in-period transactions.
5. **Bank ID is an integer key.** → Normalise to integer; the account key is `(bank_id, account_number)`
   because 8 account numbers exist at two banks.
6. **Entity type and entity ID exist.** Partnership, Corporation, Sole Proprietorship, Country, Individual,
   Direct; one entity holds up to 7,820 accounts. → Expose entity type and the entity's other accounts in the
   account profile: this is the "other business relationships" view from AMLR Art. 26(1).
7. **ACH carries 87% of laundering** (4,483 of 5,177; rate 0.75% vs ≤ 0.04% for other formats; Wire and
   Reinvestment have none). This is a legitimate behavioural signal, not a leak; baseline B3 will use it, so
   the agent's advantage must be measured against it.
8. **Self-transfers are 12% of rows.** → Exclude them from fan-in, fan-out and pass-through rules.

## Open checks

- Whether bank ID order or bank country predicts the label (M2, before deciding what the profile exposes).
