#!/usr/bin/env bash
set -euo pipefail

VAULT="${1:?usage: aks-load-db.sh <key-vault-name>}"

secret() { az keyvault secret show --vault-name "$VAULT" --name "$1" --query value --output tsv; }

export AKS_OWNER_DSN AKS_MCP_DSN AKS_APP_DSN AKS_EVALUATOR_DSN
AKS_OWNER_DSN=$(secret aml-owner-dsn)
AKS_MCP_DSN=$(secret aml-mcp-dsn)
AKS_APP_DSN=$(secret aml-app-dsn)
AKS_EVALUATOR_DSN=$(secret aml-evaluator-dsn)

uv run python scripts/load_aks_database.py aml_sim_ui
