#!/usr/bin/env bash
# Pause and resume the Azure deployment without deleting anything.
#
#   infra/azure/power.sh status   what's running, and is the API healthy
#   infra/azure/power.sh down     stop the always-on parts (saves most of the cost)
#   infra/azure/power.sh up       start them again and wait until the API is healthy
#
# "Always-on" = the Postgres server, the worker and Redis. The api, ui and
# news apps scale to zero by themselves when idle.
#
# Note: Azure starts a stopped Postgres Flexible Server again automatically
# after 7 days. Run `down` again if it should stay off longer.
# To remove the deployment entirely: infra/azure/destroy.sh.
set -euo pipefail

RG="${RG:-rg-mia-dev}"
PG="${PG:-psql-mia-dev-bb01}"
ALWAYS_ON=(ca-mia-worker ca-mia-redis)

pg_state() { az postgres flexible-server show -g "$RG" -n "$PG" --query state -o tsv; }
replicas() {
  az containerapp replica list -g "$RG" -n "$1" --query 'length(@)' -o tsv 2>/dev/null || echo "?"
}
api_url() {
  echo "https://$(az containerapp show -g "$RG" -n ca-mia-api --query properties.configuration.ingress.fqdn -o tsv)"
}

status() {
  echo "postgres  $PG: $(pg_state)"
  for app in ca-mia-api ca-mia-worker ca-mia-news ca-mia-ui ca-mia-redis; do
    min="$(az containerapp show -g "$RG" -n "$app" --query properties.template.scale.minReplicas -o tsv)"
    printf '%-14s replicas running: %s (min %s)\n' "$app" "$(replicas "$app")" "$min"
  done
}

case "${1:-}" in
  status)
    status
    url="$(api_url)"
    echo "API health: $(curl -s -m 60 "$url/health" || echo unreachable)"
    ;;
  down)
    # Worker first: it must not be mid-job when its database goes away. Any
    # job it was running stays "running" and resumes on the next `up`.
    for app in "${ALWAYS_ON[@]}"; do
      echo "scaling $app to zero"
      az containerapp update -g "$RG" -n "$app" --min-replicas 0 -o none
    done
    if [[ "$(pg_state)" == "Ready" ]]; then
      echo "stopping postgres $PG"
      az postgres flexible-server stop -g "$RG" -n "$PG" -o none
    fi
    status
    echo "Paused. Resume with: infra/azure/power.sh up (Postgres auto-starts after 7 days)"
    ;;
  up)
    if [[ "$(pg_state)" != "Ready" ]]; then
      echo "starting postgres $PG (a few minutes)"
      az postgres flexible-server start -g "$RG" -n "$PG" -o none
    fi
    # Redis before the worker, which needs its queue at startup.
    for app in ca-mia-redis ca-mia-worker; do
      echo "scaling $app to 1"
      az containerapp update -g "$RG" -n "$app" --min-replicas 1 -o none
    done
    url="$(api_url)"
    echo "waiting for $url/health (the api wakes from zero on the first request)"
    for _ in $(seq 1 30); do
      if curl -fsS -m 30 "$url/health" 2>/dev/null; then
        echo
        status
        echo "Ready: $(echo "$url" | sed 's/ca-mia-api/ca-mia-ui/')"
        exit 0
      fi
      sleep 10
    done
    echo "API not healthy after 5 minutes; check: az containerapp logs show -g $RG -n ca-mia-api"
    exit 1
    ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
