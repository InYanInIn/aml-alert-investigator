# AML Alert Investigator

An LLM agent that prepares anti-money-laundering alert investigations for an analyst: it gathers evidence
through read-only MCP tools, tests the alert against laundering typologies, cites the transactions and
regulation passages behind every claim, verifies those citations deterministically, and waits for the analyst
to confirm or override. The agent is evaluated against labels it never sees and against non-LLM baselines.

Portfolio project on synthetic data (IBM AMLworld). Not a compliance product.

**Status:** M6 — the full agent with a deterministic stand-in model, deployed to local Kubernetes. Phase B (live
model on Azure) is next. See [docs/plan.md](docs/plan.md).

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
