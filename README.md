# AML Alert Investigator

An LLM agent that prepares anti-money-laundering alert investigations for an analyst: it gathers evidence
through read-only MCP tools, tests the alert against laundering typologies, cites the transactions and
regulation passages behind every claim, verifies those citations deterministically, and waits for the analyst
to confirm or override. The agent is evaluated against labels it never sees and against non-LLM baselines.

Portfolio project on synthetic data (IBM AMLworld). Not a compliance product.

**Status:** M0 — tooling, data profiling and SDK spike. See [docs/plan.md](docs/plan.md).

- Design: [docs/design.md](docs/design.md)
- Decisions: [docs/adr/](docs/adr/)
- Data findings: [docs/data-notes.md](docs/data-notes.md)
