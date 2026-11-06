# Regulatory corpus

The two legal texts the agent cites (ADR-0005). They are committed as downloaded, with their checksums in
`sources/sources.json`, so every passage ID can be checked against the official text.

| doc_id | Text | Version | File |
|---|---|---|---|
| `AMLR` | Regulation (EU) 2024/1624 of 31 May 2024 on the prevention of the use of the financial system for the purposes of money laundering or terrorist financing (English) | OJ L, 2024/1624, 19.6.2024, CELEX 32024R1624 | `sources/amlr-2024-1624.en.xhtml` |
| `PL-AML` | Ustawa z dnia 1 marca 2018 r. o przeciwdziałaniu praniu pieniędzy oraz finansowaniu terroryzmu (Polish) | Consolidated text Dz.U. 2025 poz. 644 (obwieszczenie Marszałka Sejmu z 9 maja 2025 r.) | `sources/pl-aml-2025-644.pdf` |

Sources:

- AMLR: `https://publications.europa.eu/resource/celex/32024R1624` with `Accept: application/xhtml+xml` and
  `Accept-Language: eng` (the Publications Office/Cellar copy of the Official Journal; EUR-Lex itself blocks scripted
  downloads).
- PL-AML: `https://api.sejm.gov.pl/eli/acts/DU/2025/644/text/T/D20250644L.pdf` (official text, file type `T`,
  Sejm ELI API).

## Acknowledgement and licence

- AMLR: © European Union, https://eur-lex.europa.eu/. Reused under Commission Decision 2011/833/EU on the reuse of
  Commission documents; the source is acknowledged and the meaning is not distorted. Only the Official Journal
  edition is authentic.
- PL-AML: Polish normative acts are not subject to copyright (art. 4 pkt 1 ustawy z dnia 4 lutego 1994 r. o prawie
  autorskim i prawach pokrewnych, Dz.U. 2025 poz. 24). Source: Kancelaria Sejmu, ELI API.

## Passages

One passage per numbered paragraph; an article without numbered paragraphs is one passage. Points stay inside their
paragraph, except the two definition lists (`AMLR art. 2(1)`, `PL-AML art. 2 ust. 2`), which are one passage per
point. IDs: `AMLR art. 77(1)`, `AMLR art. 3`, `PL-AML art. 74 ust. 1`, `PL-AML art. 67`, `AMLR art. 2(1)(28)`, `PL-AML art. 2 ust. 2 pkt 12a`. Recitals, annexes,
chapter headings, page headers and footnotes are not passages; repealed or omitted provisions (`(uchylony)`,
`(pominięte)`) are skipped.

The Polish text is extracted from the PDF: body text is kept by font size and page position, hyphenated line
breaks are joined (keeping the hyphen of compounds such as `oszczędnościowo-kredytowe`), and words that the PDF
splits with a spurious space (`p raniu`) are rejoined when the joined word occurs elsewhere in the act.

## Commands

```
uv run aml corpus fetch                         # download missing files, verify every file against sources.json
uv run aml --database aml_hi_small corpus load  # replace corpus.documents/passages for both documents
```

`fetch` never overwrites a file: an existing file whose sha256 differs from the manifest is an error.

## Refreshing a source

1. Delete the file in `sources/` and its entry in `sources/sources.json`.
2. Update `version` (and the URL if the consolidated text changed) in `src/aml_investigator/corpus/sources.py`.
3. Run `aml corpus fetch`, then `corpus load` for each database, and `pytest tests/test_corpus.py`; check that every
   expected passage in `eval/retrieval/queries.json` still exists and still answers its query.
