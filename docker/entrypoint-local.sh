#!/bin/sh
# Local dev only. The host's ~/.azure is mounted read-only at /mnt/host-azure.
# `az` rewrites its token cache when it refreshes a token, so work from a
# writable copy; the host directory is never modified.
set -e

if [ -d /mnt/host-azure ]; then
    mkdir -p "$AZURE_CONFIG_DIR"
    cp -R /mnt/host-azure/. "$AZURE_CONFIG_DIR"/
fi

exec "$@"
