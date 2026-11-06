# AML Alert Investigator

An LLM agent that prepares anti-money-laundering alert investigations for an analyst: it gathers evidence
through read-only MCP tools, tests the alert against laundering typologies, cites the transactions and
regulation passages behind every claim, verifies those citations deterministically, and waits for the analyst
to confirm or override. The agent is evaluated against labels it never sees and against non-LLM baselines.

Portfolio project on synthetic data (IBM AMLworld). Not a compliance product.

**Status:** M7 — the agent runs on Azure OpenAI `gpt-5-mini` (about 1.6 cents and 40 seconds per alert), a
local open-weight model or a deterministic stand-in; regulations are retrieved by vector search per law; CI
replays recorded investigations offline. Results in [docs/results.md](docs/results.md). Next: M8, evaluation
on the test split. See [docs/plan.md](docs/plan.md).

## Run the agent on Azure OpenAI
Needs the Azure resources from `infra/terraform` (`terraform apply` with `budget_email` in a git-ignored
`terraform.tfvars`), `az login` as a user holding `Cognitive Services OpenAI User`, and the endpoint in `.env`
as `AML_AZURE_OPENAI_ENDPOINT`. There are no API keys: the account accepts Entra ID tokens only.

```bash
uv run aml --database aml_hi_small corpus embed
uv run aml --database aml_hi_small eval run --system azure --split val --limit 10 --concurrency 2 --record
uv run aml --database aml_hi_small eval retrieval
```


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
