# ADR-0005: Regulatory corpus — EU AMLR and the Polish AML Act; FATF cited by reference only

Status: accepted · 2026-10-28 · deviates from the brief

## Context
The brief proposed RAG over FATF recommendations and EU legislation.

- FATF publications state "All rights reserved" and forbid reproduction without written permission.
- EUR-Lex content may be reused for commercial or non-commercial purposes provided the source is acknowledged
  and the meaning is not distorted (Commission Decision 2011/833/EU, Art. 3 and 6). EUR-Lex blocks scripted
  downloads with a bot challenge.
- Polish normative acts are excluded from copyright (ustawa o prawie autorskim i prawach pokrewnych, art. 4
  pkt 1; Dz.U. 2025 poz. 24). The current official consolidated text of the Polish AML Act (ustawa z dnia
  1 marca 2018 r. o przeciwdziałaniu praniu pieniędzy oraz finansowaniu terroryzmu) is Dz.U. 2025 poz. 644,
  published as PDF only through the Sejm ELI API.
- Regulations state obligations (monitor, assess, record, report), not detection rules.

## Decision
- Corpus:
  - Regulation (EU) 2024/1624 (AMLR), English, from EUR-Lex.
  - Polish AML Act, Polish, consolidated text Dz.U. 2025 poz. 644 (`T` file from
    `api.sejm.gov.pl/eli/acts/DU/2025/644`). The non-binding unified text with later amendments is not used.
- Both texts are extracted once and committed with source and version; `corpus.documents` stores the version.
- Passages are split by article and paragraph; IDs are legal citations, e.g. `AMLR art. 77(1)(b)` and
  `PL-AML art. 74 ust. 1`.
- FATF material is referenced by recommendation number only, never stored.
- RAG justifies why an alert must be assessed and what the record must contain; typology detection comes from
  the rules and detectors.

## Consequences
- Citations are human-verifiable against official texts.
- Queries are English while the Polish passages are Polish; cross-lingual retrieval quality is measured in M4
  with a small hand-labelled query set before relying on it.
- The AMLR applies from 10 July 2027; until then the Polish Act is the law in force. The rationale cites both
  where they cover the same obligation.
- EBA guidelines are out of scope until their reuse terms are checked.
