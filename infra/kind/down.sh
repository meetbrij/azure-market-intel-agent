#!/usr/bin/env bash
# Delete the local kind cluster (and with it the demo Postgres data).
set -euo pipefail
kind delete cluster --name "${CLUSTER:-mia}"
