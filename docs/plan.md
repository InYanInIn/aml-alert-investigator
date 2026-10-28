# AML Alert Investigator — Plan

Status: draft for approval · 2026-10-28

Each milestone is a thin slice that runs end to end and ends with a check that proves it. Numbers marked
"N" are measured in that milestone and written into `docs/data-notes.md` or `docs/results.md`; nothing is
claimed before it is measured.

Phase A runs locally with a fake model and costs $0. Phase B starts when the Azure account is created and
must finish within its 30-day credit window (ADR-0009).

## Phase A — local, $0

### M0 — Tooling, data inspection, SDK spike
- Public GitHub repo (ADR-0014) with uv, Python 3.12, ruff, mypy (strict), pytest; GitHub Actions PR workflow; `docker compose` with
  `pgvector/pgvector:pg16`.
- You download HI-Small and LI-Small (`*_Trans.csv`, `*_Patterns.txt`, `*_accounts.csv`) from Kaggle.
- Inspect and record in `docs/data-notes.md`: `Patterns.txt` block format, `*_accounts.csv` columns,
  laundering counts, number and labels of post-period transactions, whether account-ID order correlates with
  laundering.
- Spike: a FastMCP server with one tool, called from a LangGraph node through `langchain.mcp` (ADR-0006).

Check: CI green on an empty package; `data-notes.md` contains the measured counts (resolving Kaggle 5.1K vs
paper 3.6K for HI-Small); the spike test calls the tool and gets a typed result.

### M1 — Thin vertical slice
- Ingest the first 100K transactions of HI-Small (by `ts`) with stable IDs; labels into `truth`.
- Roles `owner`, `mcp_reader`, `app`, `evaluator` created by migration.
- Rule R2 (fan-out) only → alerts.
- MCP tools `get_alert`, `list_transactions`.
- Graph `load_alert → build_evidence_pack → draft_decision → verify_citations → persist → human_review →
  record_decision` with a scripted fake model that cites a real transaction ID.
- One UI page: alert, evidence, recommendation, confirm/override form.

Check: one command ingests 100K rows; R2 yields N > 0 alerts; investigating one alert stores an investigation
with ≥ 1 verified citation; confirming in the UI inserts exactly one `review.decisions` row; `mcp_reader` and
`app` get `permission denied` on every `truth` table.

### M2 — Full monitor and ground truth
- Full HI-Small and LI-Small ingest, post-period tail dropped.
- Rules R1–R6 with unit tests on hand-built transaction graphs (one positive and one negative fixture per
  rule); thresholds calibrated on the train window.
- `truth.alert_truth` with both ground-truth variants and `typology_truth`.

Check: `data-notes.md` reports per dataset: alerts per rule, prevalence of `escalate_truth`, share of pattern
attempts touched by any alert (rule recall per pattern), alerts per day. All rule tests pass. Decision
recorded on whether 10 days of history are enough for R6 (else open HI-Medium question).

### M3 — Evaluation harness and non-LLM baselines
- Chronological 60/20/20 split; frozen stratified samples committed as alert-ID lists.
- Metrics from design §8 with bootstrap intervals; B0, B1, B3.

Check: `eval run --system b3 --split val` prints the metrics table with intervals and writes a JSON result;
re-running produces identical numbers; the test split cannot be opened without an explicit flag that is logged.

### M4 — MCP server, corpus, references and injection set
- All tools from design §6 with limits and server-side `as_of`.
- Synthetic `reference` field; `reference_injections` for ~40 pairs; `REFERENCE_VARIANT` switch.
- AMLR (EUR-Lex) and Polish AML Act (Dz.U. 2025 poz. 644, Sejm ELI PDF) committed with source and version;
  passages split by article/paragraph; loader without embeddings yet.
- A hand-labelled set of ~20 English queries with the expected AMLR and PL-AML passages, used in M7 to measure
  retrieval (including cross-lingual) once embeddings exist.

Check: contract tests per tool (row limits hold, no row after `as_of`, no truth field in any response);
reference independence test passes; corpus contains N passages; `AMLR art. 77(1)(b)` and
`PL-AML art. 74 ust. 1` resolve to the expected text.

### M5 — Full agent with fake model, verifier, UI
- Architecture C: evidence pack, bounded loop (`k`, token budget), regulation retrieval, structured
  decision, verifier rules 1–5, one repair, forced escalation on second failure.
- `PostgresSaver` checkpoints; interrupt/resume.
- UI: alert queue, detail with clickable citations, confirm/override.
- Tracing hook wired, inactive without keys.

Check: graph tests with scripted fake model cover: valid path; invalid citation → repair → pass; two failures
→ stored as `failed` and `escalate`; loop stops at `k`; resume after restarting the process continues the same
thread. Verifier has unit tests for each rule.

### M6 — Containers and Kubernetes locally
- Dockerfiles for `api` and `mcp`; Helm chart; deploy to kind against the compose Postgres.

Check: `helm install` on kind; a smoke test through port-forward loads the queue, runs one fake-model
investigation and records one decision.

## Phase B — Azure credit window (≤ 30 days)

### M7 — Live model and cost calibration
- You create the Azure account and upgrade to pay-as-you-go. Terraform (first part): resource group, Azure
  OpenAI with `gpt-5-mini` (Data Zone EU or Global Standard), embeddings deployment, budget alerts.
- Embed the corpus; run B2 and C on 10 validation alerts; Langfuse Cloud EU traces; record responses for
  replay.

Check: retrieval hit rate on the M4 query set reported (AMLR and PL-AML separately); measured cost, tokens
and latency per alert recorded; Cost Management shows the spend drawn from credit;
traces visible; CI replay evaluation passes on the recorded responses.

### M8 — Evaluation and results
- Iterate on validation (prompt, `k`, budget) with the 3-repeat stability check.
- Open test once: C, A (`k = 0`), B0–B3 on HI-Small and LI-Small; injection pairs on C.
- Error analysis of 20 wrong decisions; manual grading of 30 regulation citations.

Check: `docs/results.md` is generated from committed JSON, lists every test-split opening, and reports every
metric with intervals — including any metric where a baseline beats the agent.

### M9 — AKS demo and teardown
- Terraform (second part): AKS Free tier with one B2s node, ACR, PostgreSQL Flexible Server B1ms with
  `vector`, Key Vault, workload identity; GitHub OIDC deploy workflow.
- Load HI-Small into Flexible Server; deploy; record the screencast via port-forward.
- `terraform destroy`; close the subscription before day 30 unless you decide otherwise.

Check: smoke test passes on AKS; one live investigation and one analyst decision recorded; after destroy, the
resource group no longer exists; final spend recorded in `docs/results.md`.

## Teardown checklist (Phase B)
- [ ] `terraform destroy` completed without errors
- [ ] `az group list` shows no project resource groups
- [ ] Azure OpenAI deployments deleted
- [ ] Cost Management: spend over credit = $0 (or the measured amount)
- [ ] Subscription cancelled or explicitly kept
