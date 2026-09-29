# ADR 0006: Deployment topology on Azure Container Apps

- **Status:** Accepted
- **Date:** 2026-09-29
- **Phase:** 3 (Day 18, Azure deployment and CI/CD)

## Context

The system must run in Azure with the same guarantees it has locally:
- keyless authentication;
- one worker;
- human approval;
- crash recovery;
- traces and audit.

It should cost little when idle and be reproducible from the repository.
ADR 0005 explains why this isn't AKS.

## Decision

### Topology

```
                       Internet (HTTPS only)
                  ┌───────────┴────────────┐
             ca-mia-ui                 ca-mia-api            external ingress
             (Streamlit, 0–1)          (FastAPI, 0–2)
                  └──── calls ──────────►  │
                                           │ jobs, audit            ┌─────────────────────┐
  ca-mia-worker (arq + LangGraph, exactly 1) ├──────────────────────►│ PostgreSQL Flexible │
        │  │  │                            │                        │ B1ms, Entra-only    │
        │  │  └── internal TCP ──► ca-mia-redis (queue, 1)           └─────────────────────┘
        │  └───── internal HTTP ─► ca-mia-news (MCP server, 0–1) ──► Tavily (key in Key Vault)
        └──► Azure OpenAI · AI Search · Blob (archive) · Key Vault · Langfuse
```

| App | Ingress | Replicas | Identity (roles) |
|---|---|---|---|
| `ca-mia-api` | external HTTPS | 0–2 | `id-mia-api`: Search Index Data Reader, Storage Blob Data Reader; `id-mia-db` |
| `ca-mia-worker` | none | exactly 1 | `id-mia-worker`: Cognitive Services OpenAI User, Search Index Data Reader, Storage Blob Data Contributor, Key Vault Secrets User; `id-mia-db` |
| `ca-mia-news` | internal HTTP | 0–1 | `id-mia-news`: Key Vault Secrets User |
| `ca-mia-ui` | external HTTPS | 0–1 | `id-mia-ui` (image pull only) |
| `ca-mia-redis` | internal TCP 6379 | 1 | image pull only |

Every identity also has AcrPull on the registry.

### Keyless everywhere, including the database

- **Postgres** has password authentication **disabled**. Apps log in as a
  role mapped to the `id-mia-db` managed identity, and their Entra token is
  the password: a fresh one for every new connection, for both asyncpg and
  psycopg (`app/db_auth.py`).
  - The role is created once by `infra/azure/db_setup.py`, run as the
    server's Entra admin (you, with an `az login` token).
  - So **no database password exists** anywhere: not in Key Vault, not in
    the pipeline, not in any app setting.
- **Registry:** images are pulled by each app's identity (AcrPull); the
  registry's admin user is off.
- **Pipeline:** it authenticates by workload identity federation, with no
  client secret.
- **Secrets:** the only third-party secrets, Tavily and Langfuse, stay in
  Key Vault and are read at runtime by the one identity that needs each.

### User-assigned identities (a deviation from the spec)

The spec asks for **system-assigned** identities. I used **user-assigned**
ones:
- **Pull order:** a system-assigned identity exists only once its app does,
  so its AcrPull grant can't be in place before the first revision tries to
  pull its image, and that first deployment fails. User-assigned identities
  and their roles are created first, in the base deployment, so the first
  revision pulls cleanly.
- **Shared database role:** the api and worker must share one Postgres role.
  Both create and alter the tables at startup, and ALTER needs ownership. A
  shared `id-mia-db` identity, attached to both next to their own identities,
  gives them one owner without also sharing their other permissions.

### Other choices

- **One worker:** `minReplicas = maxReplicas = 1` (D-25).
- **Redis** runs as a container app on internal TCP, which Container Apps
  allows without a custom virtual network. It has no persistence: it's a
  queue, and job state lives in Postgres.
- **The news server** has internal ingress only, and DNS-rebinding
  protection allows only its own names.
- **Scale to zero:** the api, ui and news scale to zero when idle, so the
  always-on cost is the worker, Redis and Postgres.
- **Postgres networking:** the server is reached over its public endpoint
  with TLS required, from "Azure services" plus the admin's IP. A private
  endpoint needs a virtual network, which costs more than the demo warrants
  (see the limitations).
- **Infrastructure as code:** Bicep (`infra/azure/main.bicep`), deployed in
  two passes by `infra/azure/deploy.sh`.
  1. The base: registry, database, identities, roles, environment.
  2. Images are built for linux/amd64 with `docker buildx` and pushed (ACR
     Tasks is blocked on this subscription), then the database role is
     created, then the apps are deployed.
  - `deploy.sh --what-if` previews the base without creating anything.

### CI/CD (Azure Pipelines)

`infra/azure-pipelines.yml` runs four stages:
1. **Test:** ruff, mypy and pytest, offline.
2. **Eval gate:** `evals.run --smoke` with real model calls; it fails below
   `evals/thresholds.yaml`.
3. **Build:** `docker build` and `push` of both images on the amd64 agent,
   tagged with the build id and `latest`. Runs on `main` only.
4. **Deploy:** `az containerapp update` of each app to the new tag, then
   `/health` is polled. Runs on `main` only.

## Consequences

- **First request after idle:** with the api at zero replicas, it waits for
  a cold start (about 10–20 s).
- **Worker rollout:** a new revision starts before the old one stops, so a
  deploy during a running job can briefly run two workers, which D-25
  warns about. Container Apps has no Recreate strategy for single-revision
  apps. Deploy between jobs; recorded in the limitations.
- **Worker health:** there's no exec probe in Container Apps, so the
  worker's arq health check isn't used there. The platform restarts the
  worker if it exits.
- **Postgres is on a public endpoint**, protected by Entra-only auth,
  TLS and the firewall, but not by network isolation.
