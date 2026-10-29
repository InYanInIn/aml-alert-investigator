# ADR-0008: Langfuse Cloud EU instead of self-hosting

Status: accepted · 2026-10-28 · deviates from the brief

## Context
Self-hosted Langfuse needs Postgres, ClickHouse, Redis/Valkey and S3-compatible storage plus web and worker
services — too heavy for a one-node demo cluster. The free Hobby cloud plan offers 50K units per month, two
users and an EU data region.

## Decision
Use Langfuse Cloud (EU region) through the LangChain callback. Tracing is off when its keys are absent.

## Consequences
- Synthetic traces leave our environment; acceptable here, not for real data. A bank would self-host.
- 30-day data access on the free plan; evaluation results are stored in the repo, not only in Langfuse.
