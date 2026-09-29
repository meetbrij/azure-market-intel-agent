#!/usr/bin/env bash
# Tear down the Azure deployment (Day 18) — the reverse of deploy.sh.
#
#   infra/azure/destroy.sh --dry-run    list what would be deleted; delete nothing
#   infra/azure/destroy.sh              asks you to type the resource group name
#   infra/azure/destroy.sh --yes        no prompt (scripts)
#
# Deletes only what deploy.sh created:
#   container apps, the Container Apps environment, Log Analytics (purged),
#   the Postgres server (its jobs, audit and checkpoint data), the registry
#   (its images), the five managed identities, and their role assignments on
#   the Day 1 resources.
# Keeps the Day 1 resources and everything in them: Azure OpenAI and its
# deployments, AI Search and the filings index, Storage (filings + report
# archive), Key Vault (Tavily/Langfuse keys). Also keeps the budget alert, the
# Entra app registrations and the pipeline's service connection.
# Redeploy any time with infra/azure/deploy.sh.
set -euo pipefail

RG="${RG:-rg-mia-dev}"
SUFFIX="${SUFFIX:-dev-bb01}"
MODE="${1:-}"

APPS=(ca-mia-api ca-mia-ui ca-mia-worker ca-mia-news ca-mia-redis)
ENV_NAME="cae-mia-$SUFFIX"
LOGS="log-mia-$SUFFIX"
PG="psql-mia-$SUFFIX"
ACR="acrmia${SUFFIX//-/}"
IDENTITIES=(id-mia-api id-mia-worker id-mia-news id-mia-ui id-mia-db)

exists() { az resource show -g "$RG" -n "$1" --resource-type "$2" --query id -o tsv 2>/dev/null; }

SUB="$(az account show --query id -o tsv)"
# All role assignments of a principal, at any scope in the subscription.
# (Queried directly: `az role assignment list --all` fails when the CLI has a
# default resource group configured.)
assignments_of() {
  az rest --method get \
    --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Authorization/roleAssignments?api-version=2022-04-01&\$filter=principalId%20eq%20'$1'" \
    --query "value[].id" -o tsv
}

echo "Resource group: $RG"
plan=()
for app in "${APPS[@]}"; do
  [[ -n "$(exists "$app" Microsoft.App/containerApps)" ]] && plan+=("container app   $app")
done
[[ -n "$(exists "$ENV_NAME" Microsoft.App/managedEnvironments)" ]] && plan+=("environment     $ENV_NAME")
[[ -n "$(exists "$LOGS" Microsoft.OperationalInsights/workspaces)" ]] && plan+=("log analytics   $LOGS (purged)")
[[ -n "$(exists "$PG" Microsoft.DBforPostgreSQL/flexibleServers)" ]] && plan+=("postgres        $PG (jobs, audit, checkpoints)")
[[ -n "$(exists "$ACR" Microsoft.ContainerRegistry/registries)" ]] && plan+=("registry        $ACR (images)")
principals=()
for identity in "${IDENTITIES[@]}"; do
  pid="$(az identity show -g "$RG" -n "$identity" --query principalId -o tsv 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    principals+=("$pid")
    n="$(assignments_of "$pid" | grep -c . || true)"
    plan+=("identity        $identity (+ $n role assignment(s))")
  fi
done

if [[ ${#plan[@]} -eq 0 ]]; then
  echo "Nothing to delete: the deployment isn't there."
  exit 0
fi
printf '  %s\n' "${plan[@]}"
echo "Kept: OpenAI, AI Search (index), Storage (filings, reports), Key Vault, budget, Entra apps."

if [[ "$MODE" == "--dry-run" ]]; then
  echo "(dry run: nothing deleted)"
  exit 0
fi
if [[ "$MODE" != "--yes" ]]; then
  read -r -p "Type the resource group name to delete the above: " answer
  [[ "$answer" == "$RG" ]] || { echo "Aborted."; exit 1; }
fi

# Apps first (they use everything else), then the environment and its logs.
for app in "${APPS[@]}"; do
  if [[ -n "$(exists "$app" Microsoft.App/containerApps)" ]]; then
    echo "deleting container app $app"
    az containerapp delete -g "$RG" -n "$app" --yes -o none
  fi
done
if [[ -n "$(exists "$ENV_NAME" Microsoft.App/managedEnvironments)" ]]; then
  echo "deleting environment $ENV_NAME"
  az containerapp env delete -g "$RG" -n "$ENV_NAME" --yes -o none
fi
if [[ -n "$(exists "$LOGS" Microsoft.OperationalInsights/workspaces)" ]]; then
  echo "deleting log analytics $LOGS"
  # --force purges now; otherwise the name stays reserved for 14 days.
  az monitor log-analytics workspace delete -g "$RG" -n "$LOGS" --force --yes -o none
fi
if [[ -n "$(exists "$PG" Microsoft.DBforPostgreSQL/flexibleServers)" ]]; then
  echo "deleting postgres $PG"
  az postgres flexible-server delete -g "$RG" -n "$PG" --yes -o none
fi
if [[ -n "$(exists "$ACR" Microsoft.ContainerRegistry/registries)" ]]; then
  echo "deleting registry $ACR"
  az acr delete -g "$RG" -n "$ACR" --yes -o none
fi

# Role assignments before identities, so none are left pointing at nothing.
for pid in "${principals[@]}"; do
  ids="$(assignments_of "$pid")"
  if [[ -n "$ids" ]]; then
    echo "removing role assignments of $pid"
    # shellcheck disable=SC2086
    az role assignment delete --ids $ids -o none
  fi
done
for identity in "${IDENTITIES[@]}"; do
  if az identity show -g "$RG" -n "$identity" -o none 2>/dev/null; then
    echo "deleting identity $identity"
    az identity delete -g "$RG" -n "$identity" -o none
  fi
done

echo "Done. Redeploy with infra/azure/deploy.sh."
