#!/usr/bin/env bash
set -euo pipefail

CLUSTER=aml
CONTEXT="kind-$CLUSTER"
RELEASE=aml
DATABASE="${1:-aml_hi_small}"

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 60s

docker build -t aml-investigator:dev .
TAG=$(docker image inspect -f '{{.Id}}' aml-investigator:dev | cut -d: -f2 | cut -c1-12)
docker tag aml-investigator:dev "aml-investigator:$TAG"
kind load docker-image "aml-investigator:$TAG" --name "$CLUSTER"

set -a
. ./.env
set +a
from_host() { printf '%s=%s\n' "$1" "${2/127.0.0.1/host.docker.internal}"; }
kubectl --context "$CONTEXT" create secret generic aml-api \
  --from-env-file=<(from_host AML_APP_DSN "$AML_APP_DSN") --dry-run=client -o yaml | kubectl --context "$CONTEXT" apply -f -
kubectl --context "$CONTEXT" create secret generic aml-mcp \
  --from-env-file=<(from_host AML_MCP_DSN "$AML_MCP_DSN") --dry-run=client -o yaml | kubectl --context "$CONTEXT" apply -f -

helm upgrade --install "$RELEASE" deploy/helm/aml-investigator --kube-context "$CONTEXT" \
  --set image.tag="$TAG" --set database="$DATABASE" --wait --timeout 3m
kubectl --context "$CONTEXT" get pods -l app.kubernetes.io/instance="$RELEASE"
