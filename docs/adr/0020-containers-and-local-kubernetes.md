# ADR-0020: One image, two workloads, per-role secrets; readiness depends on the database only

Status: accepted · 2026-11-02

## Context
M6 packages the analyst API and the MCP data server for Kubernetes and deploys them to kind against the
compose Postgres. Until now both processes read all four role DSNs from `.env`, and the alert page returned
500 while the MCP server was unreachable.

## Decision
- **One image, two Deployments.** Both processes are the same package with the same dependencies; the image
  runs `aml` and each Deployment passes its subcommand (`api` or `mcp`). Separate images would duplicate the
  1.1 GB dependency layer without separating any code.
- **Each pod holds only its own role's DSN.** The API gets `AML_APP_DSN`, the MCP server `AML_MCP_DSN`; the
  role DSNs in `Settings` became optional and a missing one fails with `MissingSettingError` naming the
  variable. Giving the API pod the owner DSN would let it read `truth.*` and undo ADR-0004 inside the cluster.
- **Liveness and readiness are different questions.** `/healthz` answers "is the process serving" and checks
  nothing else. `/readyz` answers "can it do its job" and pings its own database pool (2 s timeout).
- **The API's readiness ignores the MCP server.** The queue and the auto-closed list read only the review
  database; if an MCP outage made the API unready, Kubernetes would remove it from its Service and analysts
  would lose those pages too, and the 503 page would never be shown. Instead every MCP call goes through one
  function that turns FastMCP's connection failure into `McpUnavailableError`, rendered as a 503 page; nothing
  is recorded, and the page recovers by itself when the MCP server is back.
- **Pods run hardened by default:** non-root UID 10001, read-only root filesystem with an `emptyDir` `/tmp`,
  no capabilities, no service-account token, `RuntimeDefault` seccomp, and `enableServiceLinks: false` —
  Kubernetes would otherwise inject variables such as `AML_MCP_PORT=tcp://…` that share our settings prefix.
- **Locally, secrets are plain Kubernetes Secrets** created by `scripts/kind-up.sh` from `.env` through process
  substitution (never on a command line), with `127.0.0.1` rewritten to `host.docker.internal`. On AKS they
  come from Key Vault (ADR-0012, M9).
- **Image tags are content-addressed** (the first 12 hex digits of the image ID), so `helm upgrade` restarts
  pods only when the image changed.

## Consequences
- An MCP call can still fail mid-request after the session is established; that path is not translated and
  would surface as a 500. Every MCP use starts with tool discovery, which is where an unreachable server fails.
- The image is 1.09 GB (polars, scikit-learn and their native libraries); not optimised.
- The kind smoke test writes one investigation and one analyst decision (`comment = 'kind smoke test'`) into
  the database it runs against.
