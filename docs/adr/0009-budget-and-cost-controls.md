# ADR-0009: Budget — $0 development, spend only inside the free-credit window

Status: accepted · 2026-10-28 · deviates from the brief

## Context
Personal budget is $5 for the whole project. A new Azure account gets $200 credit for 30 days; unused credit
survives an upgrade to pay-as-you-go until day 30. Free-trial subscriptions reportedly have zero Azure OpenAI
quota (unverified), so the account must be upgraded. 12-month free services include PostgreSQL Flexible
Server B1MS (750 h, 32 GB) and one ACR Standard; AKS cluster management is free.

## Decision
- Build every deterministic part first with a fake model, at $0. Create the Azure account only then.
- Upgrade to pay-as-you-go immediately; check in Cost Management that model usage draws on the credit.
- Hard token caps per alert and per run in code; Azure budget alerts at $1/$3/$5 over credit.
- CI uses replayed model responses; live evaluation is a manual action.
- AKS exists for one recorded demo, reached by port-forward (no load balancer), then destroyed.

## Consequences
- All model work, evaluation and the demo must fit in 30 days; the plan orders milestones accordingly.
- Azure budgets notify but do not stop spend (to be confirmed); the teardown checklist is mandatory.
