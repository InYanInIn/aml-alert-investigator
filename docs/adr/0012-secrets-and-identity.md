# ADR-0012: Secrets — .env locally, Key Vault with workload identity on AKS, OIDC in CI

Status: accepted · 2026-10-28

## Decision
- Local: git-ignored `.env`; `.env.example` lists names only.
- AKS: Key Vault secrets mounted by the Secrets Store CSI driver using workload identity.
- GitHub Actions: OIDC federation to Azure for ACR push and `helm upgrade`; no stored cloud credentials.
- No secret value is ever committed, logged or printed.

## Consequences
- More Terraform than a plain Kubernetes Secret, but no long-lived credentials exist anywhere.
