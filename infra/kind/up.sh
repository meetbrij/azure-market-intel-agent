#!/usr/bin/env bash
# Local Kubernetes: a kind cluster running the Helm chart (Day 17).
#
#   infra/kind/up.sh            create/update cluster, images, release
#   infra/kind/down.sh          delete the cluster
#
# 1. A kind cluster whose node mounts your ~/.azure read-only at /host-azure,
#    so pods can reuse your `az login` (local only; Azure uses workload
#    identity instead).
# 2. Builds the `local` app image (Azure CLI inside) and the UI image, and
#    loads them into the node: no registry needed.
# 3. A ConfigMap `mia-env` from .env: endpoints, deployment names and Entra
#    ids. .env holds no secrets (keys live in Key Vault).
# 4. helm upgrade --install with values-local.yaml, waiting for readiness.
set -euo pipefail

CLUSTER="${CLUSTER:-mia}"
RELEASE="${RELEASE:-mia}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

[[ -f .env ]] || { echo ".env not found (see .env.example)"; exit 1; }
[[ -d "$HOME/.azure" ]] || { echo "~/.azure not found: run az login first"; exit 1; }

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --wait 120s --config - <<YAML
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraMounts:
      - hostPath: $HOME/.azure
        containerPath: /host-azure
        readOnly: true
YAML
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

docker build --target local -t azure-market-intel-agent:local .
docker build -f ui/Dockerfile -t azure-market-intel-agent-ui:local .
kind load docker-image --name "$CLUSTER" \
  azure-market-intel-agent:local azure-market-intel-agent-ui:local

# Comments and blanks dropped; connection settings come from the chart.
grep -vE '^[[:space:]]*(#|$)' .env | grep -vE '^(DATABASE_URL|REDIS_URL)=' \
  | kubectl create configmap mia-env --from-env-file=/dev/stdin \
      --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install "$RELEASE" infra/helm -f infra/helm/values-local.yaml \
  --wait --timeout 10m

kubectl get pods -l "app.kubernetes.io/instance=$RELEASE"
