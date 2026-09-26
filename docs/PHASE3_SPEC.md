# Phase 3 Spec — Evaluation, Governance and Production Deployment

Save as `docs/PHASE3_SPEC.md`. Days 13–18. Assumes Phase 2 (`phase-2-agents`)
and the Day 12 Streamlit client are complete.

## Goal

Make retrieval quality measurable, put enterprise controls around the service,
and get it running in Azure behind an automated pipeline.

**Done when:** a push to `main` on GitHub builds, tests, runs an eval gate, and
deploys api + worker to Azure Container Apps; an authenticated request against
the cloud URL produces a report; and you have a before/after retrieval quality
table with real numbers.

## Deferred to after Day 18 (deliberately)

Chunking strategy comparison, MLflow tracking, Airflow DAG, cross-job long-term
memory store. Track these in `docs/ROADMAP.md` with a sentence each on the
intended approach — they are increments, not omissions.

---

## Day 13 — Golden set and baseline

### Golden set (`evals/golden_set.yaml`)

30 question/answer pairs over your indexed filings. Write them by hand from the
documents — generating them with the same model you are evaluating is circular.
Mix:

- 12 single-document factual (revenue in a period, segment naming)
- 8 cross-document comparative (company A vs B on one metric)
- 5 temporal (change between quarters)
- 5 unanswerable — the answer is genuinely not in the corpus

Each entry:

```yaml
- id: q001
  question: "What was Amazon's AWS segment operating income in the most recent quarter reported?"
  ground_truth: "..."
  expected_sources: ["AMAZON.COM, INC. 10-Q 2026-06-30.pdf"]
  category: factual
  answerable: true
```

The unanswerable set matters most: it is how you catch a system that invents
numbers, which is the failure mode that disqualifies a tool like this in BFSI.

### Eval harness (`evals/run.py`)

CLI: `uv run python -m evals.run [--smoke] [--variant vector|hybrid] [--out results/]`

For each question: run retrieval only (not the full graph — you are measuring
the retriever), then run a single-shot answer using that context, and score
with RAGAS:

- **Context precision** and **context recall** — retrieval quality
- **Faithfulness** — is the answer grounded in the retrieved context
- **Answer relevancy** — does it address the question

Check the installed RAGAS version's API before writing the code; metric class
names and the `evaluate()` signature have changed across major versions.

Add two checks of your own in plain Python, which matter more than the LLM
metrics for your use case:

- **Citation validity** — every reference id in the output exists in the
  retrieved set. Percentage valid.
- **Abstention rate** — on the `answerable: false` questions, did it correctly
  decline rather than fabricate. This should be near 100%.

Output a JSON results file plus a markdown table. Record model name, deployment,
variant, retrieval params and timestamp in the results so runs are comparable.

`--smoke` runs 5 fixed questions and exits non-zero if faithfulness or citation
validity falls below the thresholds in `evals/thresholds.yaml`. This is what CI
calls.

**Set thresholds ~10% below your measured baseline.** A gate that trips on
normal model variance gets disabled within a week.

### Baseline run

Run the full 30 against the current vector-only retrieval. Commit results to
`results/baseline-vector.json`. Do not tune anything yet.

---

## Day 14 — Hybrid search and semantic ranker

### Index changes

Update `ingestion/index_schema.py`:

- Add a semantic configuration naming the title/content fields.
- Keep the existing vector profile.
- Reindex (`--recreate`) — schema changes to search configuration require it.

### Retrieval changes (`app/graph/retrieval.py`)

Make the retrieval mode a parameter (`vector`, `hybrid`, `hybrid_semantic`) read
from config so evals can switch variants without code changes:

- **hybrid** — pass both `search_text` (BM25) and `vector_queries` in one call.
  Azure fuses the result sets with RRF.
- **hybrid_semantic** — hybrid plus `query_type="semantic"` and the semantic
  configuration name, which reranks the fused top results.

Note the Free tier's semantic ranker quota (a limited number of queries per
month). A 30-question eval run is fine; a tight loop of repeated runs is not.
If you exhaust it, the API returns an error rather than silently degrading —
handle it by falling back to hybrid and logging a warning.

### Re-run and record

Run all three variants. Produce `docs/retrieval-benchmark.md` with a table:

| variant | context precision | context recall | faithfulness | citation validity | abstention | p95 latency | cost/query |
|---|---|---|---|---|---|---|---|

Write two paragraphs interpreting it: which variant wins on quality, what it
costs in latency, and which you shipped and why. **This document is one of the
highest-value artifacts in the whole project** — it is direct evidence for the
JD's "explain trade-offs around cost, latency and accuracy" requirement.

Set the winning variant as the default and re-run `--smoke` to fix the
thresholds.

---

## Day 15 — Security and governance

### Authentication

Register an Entra ID app for the API (Expose an API, define an app role or
scope). Validate bearer tokens in FastAPI middleware: verify signature against
the tenant JWKS, plus issuer, audience and expiry.

Keep a `DEV_AUTH_BYPASS` config flag, defaulting to **false**, for local work.
Assert at startup that it is false whenever `ENVIRONMENT != "local"` — a bypass
that can be switched on in production is worse than no bypass.

### Authorisation

Two app roles:

- `analyst` — submit research, view own jobs
- `approver` — everything analyst can do, plus `POST /research/{id}/resume`

Enforce with a FastAPI dependency. The Phase 2 approval gate now has real
meaning: a different principal approves than the one who submitted.

### Secrets

Replace any remaining local key handling with Key Vault reads via
`DefaultAzureCredential`, which resolves to your CLI identity locally and to a
managed identity in Azure. No secrets in env vars in the deployed environment,
only endpoints and names.

### Prompt-injection screen

Before news content enters any prompt: strip HTML, truncate, and run a
classifier pass (a cheap model call) that flags instruction-like content. On a
flag, drop the item and record it in `state.degraded`. Log every flag.

Document the layered defence in `docs/adr/0004-untrusted-content.md`: content
is delimited and labelled as data, never concatenated into the instruction
block, and tool output cannot alter control flow.

### Audit log

An `audit_events` table: who submitted what, who approved, which model served
each node, token counts, timestamps. Append-only. Expose it nowhere in the API
for now; the point is that it exists.

### Streamlit client

Update it to acquire a token (device code flow via MSAL is simplest) and send it
on every call. Show the signed-in user and their role.

---

## Day 16 — Observability

Instrument with Langfuse using the LangChain callback handler, so every graph
node, LLM call and tool invocation is traced without manual spans.

- Propagate `job_id` as the trace id and tag traces with user, variant and model.
- Capture token counts and cost per node, latency per node, and the retrieval
  variant in use.
- Instrument FastAPI endpoints for request-level latency.
- Add a `/metrics` endpoint or structured JSON logs with job counts by status.

Build one Langfuse dashboard view showing cost per report, p95 job latency, and
failure rate. Screenshot it for the README.

Then run the eval smoke set once with tracing on and confirm cost per query in
Langfuse matches what your eval harness computed. Reconciling the two is a good
check that neither is lying.

---

## Day 17 — Containers and Kubernetes

### Dockerfile hardening

Multi-stage; `uv` install in the builder; copy only the virtualenv and app into
a slim runtime; non-root user; no build toolchain in the final layer;
`HEALTHCHECK`; pinned base image digest. Record the image size before and after
— it is a concrete number for the README.

### Helm chart (`infra/helm/`)

Deployments for api, worker and the MCP news server; a Service and Ingress for
api; ConfigMap for non-secret config; resource requests and limits; liveness and
readiness probes; `values.yaml` plus `values-local.yaml`.

Deploy to a local `kind` cluster. Postgres and Redis in-cluster via bitnami
charts or plain manifests — this is a demonstration environment, not production
data.

**Verify:** `kind create cluster && helm install mia infra/helm/ -f
values-local.yaml`, then port-forward and run a job end to end.

AKS is intentionally skipped: same talking point, real monthly cost. Say exactly
that in the ADR (`docs/adr/0005-kubernetes.md`), because "I chose not to spend
money on it" is a better answer than silence.

---

## Day 18 — Azure deployment and CI/CD

**Do the infrastructure in the morning.** If the pipeline is not finished by end
of day you will still have a running deployed system, which is what the demo
needs.

### Infrastructure

- **Azure Database for PostgreSQL Flexible Server**, burstable B1ms, smallest
  storage. Create the `jobs` database and the checkpointer schema. Firewall:
  allow Azure services, plus your IP for admin.
- **Azure Container Registry**, Basic tier.
- **Container Apps Environment** with three apps: `api` (external ingress),
  `worker` (no ingress), `mcp-news` (internal ingress only).
- **Redis** as a container app, or accept a small Azure Cache instance if the
  container proves unstable.
- **System-assigned managed identity** on each container app, with role
  assignments mirroring your user roles from Day 1: Cognitive Services OpenAI
  User, Search Index Data Contributor, Storage Blob Data Contributor, Key Vault
  Secrets User.

Managed identity role assignments take several minutes to propagate. Expect the
first deployment to fail with 403s and retry before assuming a misconfiguration.

### Pipeline (`infra/azure-pipelines.yml`)

Replace the placeholder. Stages:

1. **Validate** — `ruff check`, `mypy`, `pytest` with coverage. Publish results.
2. **Eval gate** — `uv run python -m evals.run --smoke`. Non-zero exits the
   build. Requires Azure credentials via the service connection, so this stage
   makes real model calls; keep it to 5 questions for cost.
3. **Build** — Docker build, tag with the build id and `latest`, push to ACR.
4. **Deploy** — update the container apps to the new image tag. Gate on the
   `main` branch only.

Use an Azure Resource Manager service connection (workload identity federation
preferred over a service principal secret). Store nothing sensitive in pipeline
variables that is not marked secret.

### Verify

- Push a trivial change to GitHub `main`; the pipeline runs and deploys.
- An authenticated request to the public API URL returns a report.
- Kill the worker container app revision mid-job; confirm it resumes.
- Check the Azure cost view; confirm nothing unexpected is running.

---

## Deliverables at the end of Day 18

- `docs/retrieval-benchmark.md` — the before/after table and interpretation
- `docs/adr/0004-untrusted-content.md`, `0005-kubernetes.md`,
  `0006-deployment-topology.md`
- `docs/ROADMAP.md` — the four deferred items with intended approach
- README: architecture diagram, demo GIFs (submit → approve → report; crash →
  resume), Langfuse dashboard screenshot, build badge, live URL if you keep it up
- Resume metrics: retrieval lift from hybrid search, faithfulness and abstention
  rates, cost per report, p95 latency, image size reduction, resume-after-crash
- Tag `phase-3-production`

## Cost control

After the demo clips are recorded, scale the container apps to zero replicas and
stop the Postgres server. Restart before interviews. Check the budget alert is
still active.
