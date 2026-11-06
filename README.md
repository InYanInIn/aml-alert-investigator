# AML Alert Investigator

An LLM agent that prepares anti-money-laundering alert investigations for an analyst: it gathers evidence
through read-only MCP tools, tests the alert against laundering typologies, cites the transactions and
regulation passages behind every claim, verifies those citations deterministically, and waits for the analyst
to confirm or override. The agent is evaluated against labels it never sees and against non-LLM baselines.

Portfolio project on synthetic data (IBM AMLworld). Not a compliance product.

**Status:** M6.6 — the full agent runs on a deterministic stand-in model or on a local open-weight model
(`qwen3.6:35b-a3b` via Ollama, ADR-0022); first results in [docs/results.md](docs/results.md). Next: Phase B
(live model on Azure). See [docs/plan.md](docs/plan.md).

## Run the agent on the local model
Needs Ollama serving `qwen3.6:35b-a3b-q8_0` (~38 GB; a 64 GB Apple-silicon Mac) and a migrated, ingested
database. About 12 minutes per alert.

```bash
ollama pull qwen3.6:35b-a3b-q8_0
uv run aml --database aml_hi_small eval run --system local --split val --limit 10 --concurrency 1
uv run aml --database aml_hi_small eval pairs --system local --limit 6 --concurrency 1
```

`aml api --model local` and `aml investigate --model local` use the same model in the analyst UI and the
batch command.

## Run on local Kubernetes (kind)
Needs Docker, `kind`, `helm`, `kubectl`, the compose Postgres running and a migrated, ingested database.

```bash
./scripts/kind-up.sh aml_hi_small
uv run python scripts/kind_smoke.py
kubectl --context kind-aml port-forward svc/aml-api 8080:8000
```

`kind-up.sh` builds the image, loads it into the `aml` cluster, creates one secret per workload from `.env`
and installs the Helm chart in `deploy/helm/aml-investigator`. The smoke test loads the queue, investigates an
alert and records one analyst decision.

- Design: [docs/design.md](docs/design.md)
- Decisions: [docs/adr/](docs/adr/)
- Data findings: [docs/data-notes.md](docs/data-notes.md)
