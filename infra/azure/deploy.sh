#!/usr/bin/env bash
# Deploy to Azure Container Apps (Day 18, ADR 0006). Safe to re-run.
#
#   infra/azure/deploy.sh --what-if     preview only; creates nothing
#   infra/azure/deploy.sh               deploy / update
#
#   1. Base: registry, Postgres (Entra-only), identities, roles, environment
#   2. Images: built with docker buildx for linux/amd64 and pushed (ACR Tasks,
#      i.e. `az acr build`, is blocked on this subscription); redis imported
#   3. Postgres role for the apps' identity (as you, the Entra admin)
#   4. Apps: api, worker, news, ui, redis
#
# Reads non-secret ids from .env (AUTH_*, AZURE_OPENAI_API_VERSION). Needs
# `az login` as an Owner of the resource group.
set -euo pipefail

RG="${RG:-rg-mia-dev}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
TEMPLATE=infra/azure/main.bicep

env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

ADMIN_OID="$(az ad signed-in-user show --query id -o tsv)"
ADMIN_NAME="$(az ad signed-in-user show --query userPrincipalName -o tsv)"
ADMIN_IP="$(curl -fsS https://api.ipify.org)"
# The commit, marked -dirty when built from uncommitted changes.
TAG="${TAG:-$(git describe --always --dirty)}"

params=(
  openAiApiVersion="$(env_value AZURE_OPENAI_API_VERSION)"
  authApiClientId="$(env_value AUTH_API_CLIENT_ID)"
  authUiClientId="$(env_value AUTH_UI_CLIENT_ID)"
  dbAdminObjectId="$ADMIN_OID"
  dbAdminName="$ADMIN_NAME"
  adminIp="$ADMIN_IP"
  imageTag="$TAG"
)

if [[ "${1:-}" == "--what-if" ]]; then
  az deployment group what-if -g "$RG" -f "$TEMPLATE" -p "${params[@]}" deployApps=false
  exit 0
fi

az provider register --namespace Microsoft.DBforPostgreSQL --wait

echo "== 1/4 base infrastructure"
az deployment group create -g "$RG" -n mia-base -f "$TEMPLATE" \
  -p "${params[@]}" deployApps=false -o none
out() { az deployment group show -g "$RG" -n mia-base --query "properties.outputs.$1.value" -o tsv; }
ACR="$(out acrName)"

echo "== 2/4 images ($TAG) in $ACR"
az acr import -n "$ACR" --source docker.io/library/redis:7 --image redis:7 --force -o none
LOGIN_SERVER="$(out acrLoginServer)"
az acr login -n "$ACR"   # your Entra identity; the registry has no admin user
docker buildx build --platform linux/amd64 --target runtime -f Dockerfile \
  -t "$LOGIN_SERVER/mia:$TAG" -t "$LOGIN_SERVER/mia:latest" --push .
docker buildx build --platform linux/amd64 -f ui/Dockerfile \
  -t "$LOGIN_SERVER/mia-ui:$TAG" -t "$LOGIN_SERVER/mia-ui:latest" --push .

echo "== 3/4 Postgres role for the apps (keyless)"
uv run python infra/azure/db_setup.py --host "$(out pgHost)" --admin "$ADMIN_NAME" \
  --role "$(out dbRoleName)" --object-id "$(out dbIdentityPrincipalId)"

echo "== 4/4 container apps"
az deployment group create -g "$RG" -n mia-apps -f "$TEMPLATE" \
  -p "${params[@]}" deployApps=true -o none

API_URL="$(az deployment group show -g "$RG" -n mia-apps --query properties.outputs.apiUrl.value -o tsv)"
UI_URL="$(az deployment group show -g "$RG" -n mia-apps --query properties.outputs.uiUrl.value -o tsv)"
echo
echo "API: $API_URL   (health: $API_URL/health)"
echo "UI:  $UI_URL   (add it as a redirect URI on mia-ui: infra/entra/setup.sh)"
