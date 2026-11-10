#!/usr/bin/env bash
set -euo pipefail

VAULT="${1:?usage: aks-load-db.sh <key-vault-name>}"
NAMESPACE="${AKS_NAMESPACE:-aml}"
export AKS_FORWARD_PORT="${AKS_FORWARD_PORT:-15432}"

secret() { az keyvault secret show --vault-name "$VAULT" --name "$1" --query value --output tsv; }

export AKS_OWNER_DSN AKS_MCP_DSN AKS_APP_DSN AKS_EVALUATOR_DSN
AKS_OWNER_DSN=$(secret aml-owner-dsn)
AKS_MCP_DSN=$(secret aml-mcp-dsn)
AKS_APP_DSN=$(secret aml-app-dsn)
AKS_EVALUATOR_DSN=$(secret aml-evaluator-dsn)

kubectl -n "$NAMESPACE" port-forward svc/postgres "$AKS_FORWARD_PORT:5432" >/dev/null &
FORWARD_PID=$!
trap 'kill "$FORWARD_PID"' EXIT

until (exec 3<>"/dev/tcp/127.0.0.1/$AKS_FORWARD_PORT") 2>/dev/null; do
  kill -0 "$FORWARD_PID"
  sleep 1
done

uv run python scripts/load_aks_database.py aml_sim_ui
