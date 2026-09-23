# Phase 1 Spec — Thin Slice

Save as `docs/PHASE1_SPEC.md`. Execute one day at a time.

## Goal

A single end-to-end path: submit a research request over HTTP, have a LangGraph
run retrieve grounded context from Azure AI Search, and return a structured,
cited report. Crude but complete. Depth comes in Phases 2–4.

**Done when:** `docker compose up` followed by a `POST /api/v1/research` and a
few polls of `GET /api/v1/research/{job_id}` returns a JSON report with
citations pointing at real filing chunks.

## Context

- Repo: `azure-market-intel-agent`, Python 3.12, `uv`, arm64 macOS.
- Azure resources already exist and are reachable with keyless auth
  (`DefaultAzureCredential`). See `scripts/smoke_test.py` for working patterns
  for every client — reuse them rather than inventing new ones.
- Source data: SEC 10-K / 10-Q PDFs already uploaded to the blob container
  `raw-filings` (Amazon, Alphabet and others).
- Env vars are in `.env` (gitignored) and mirrored in `.env.example`.

## Hard constraints

1. **No hardcoded model or deployment names.** Read them from env
   (`AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBED_DEPLOYMENT`). Azure
   retires models quickly; swapping must be a config change.
2. **Keyless auth only.** No API keys for Azure services anywhere in code.
   Non-Azure keys (Tavily, Langfuse) come from Key Vault.
3. **No LangGraph checkpointer yet.** That is Phase 2. Phase 1 uses a plain
   jobs table.
4. **No web search / news tool yet.** Phase 1 is RAG-only.
5. **No UI.** Swagger docs are the interface.
6. **Pydantic v2 everywhere** for state, API schemas and config.
7. Keep files small and single-purpose. Type hints throughout. `ruff` and
   `mypy` clean.

## Target layout

```
├── app/
│   ├── config.py            # pydantic-settings, all env access
│   ├── azure_clients.py     # cached client factories (AOAI, Search, Blob, KV)
│   ├── api/
│   │   ├── main.py          # FastAPI app + lifespan
│   │   ├── routes.py        # /research endpoints
│   │   └── schemas.py       # request/response models
│   ├── graph/
│   │   ├── state.py         # ResearchState + Report models
│   │   ├── nodes.py         # retrieve, write
│   │   ├── retrieval.py     # AI Search query wrapper
│   │   └── build.py         # StateGraph assembly, compiled graph
│   ├── jobs/
│   │   ├── models.py        # SQLAlchemy Job table
│   │   ├── store.py         # create/get/update job
│   │   └── worker.py        # arq worker + task function
├── ingestion/
│   ├── index_schema.py      # AI Search index definition
│   ├── parse.py             # blob -> text
│   ├── chunk.py             # text -> chunks
│   └── ingest.py            # CLI: parse, embed, upsert
├── scripts/smoke_test.py    # already exists
├── tests/
├── docker-compose.yml
└── Dockerfile
```

---

## Day 2 — Ingestion

**Index schema** (`ingestion/index_schema.py`), index name `filings-v1`:

| field | type | notes |
|---|---|---|
| `id` | String | key, e.g. `{doc_id}-{chunk_no}` |
| `content` | String | searchable, the chunk text |
| `content_vector` | Collection(Single) | vector field, dims from the embedding model, HNSW profile |
| `company` | String | filterable, facetable |
| `ticker` | String | filterable |
| `doc_type` | String | filterable — `10-K` / `10-Q` |
| `period` | String | filterable, sortable — e.g. `2025-12-31` |
| `source_blob` | String | retrievable |
| `chunk_no` | Int32 | retrievable |

Create it with a vector search profile using HNSW. Make index creation
idempotent: drop and recreate when `--recreate` is passed.

**Parsing** (`ingestion/parse.py`): stream each PDF from blob storage and
extract text with `pypdf`. Filenames follow
`{COMPANY} {DOC_TYPE} {PERIOD}.pdf` — parse metadata from the name, but
tolerate mismatches by falling back to `unknown`. Keep a page number with each
text block so citations can reference it.

**Chunking** (`ingestion/chunk.py`): `RecursiveCharacterTextSplitter`, roughly
1000 characters with 150 overlap. Keep it simple and configurable — Phase 3
compares this against section-aware chunking.

**Ingest CLI** (`ingestion/ingest.py`): `uv run python -m ingestion.ingest
--recreate`. Lists blobs, parses, chunks, embeds in batches (batch the
embedding calls, ~64 inputs per request, with retry on 429), uploads documents
in batches of 100 (AI Search caps batch size). Log counts per document and a
final total. Note the Free tier's ~50 MB storage limit; if it's exceeded, log
clearly rather than failing silently.

**Verify:** a small script or REPL check that runs a vector query for
"operating income growth" and prints the top 3 chunks with company and period.

---

## Day 3 — Graph

**State** (`app/graph/state.py`):

```python
class Citation(BaseModel):
    company: str
    doc_type: str
    period: str
    source_blob: str
    chunk_no: int
    quote: str          # short supporting snippet, <= 25 words

class ReportSection(BaseModel):
    heading: str
    body: str
    citations: list[Citation]

class Report(BaseModel):
    subject: str
    summary: str
    sections: list[ReportSection]

class ResearchState(BaseModel):
    query: str
    companies: list[str] = []          # optional filter
    retrieved: list[dict] = []         # raw search hits
    report: Report | None = None
    error: str | None = None
```

**Nodes** (`app/graph/nodes.py`):

- `retrieve`: embed the query, run a vector search against `filings-v1`
  (top-k 8, filter by company when provided), store hits in `state.retrieved`.
- `write`: pass the query and the numbered hits to the chat model and request
  a `Report` as structured output. Prompt rules: use only the provided context,
  never invent numbers, every section must carry at least one citation, and say
  explicitly when the context is insufficient.

**Build** (`app/graph/build.py`): `StateGraph(ResearchState)`, edges
`START -> retrieve -> write -> END`, exported as a compiled `graph`.

**Note on the chat call:** GPT-5-family reasoning models reject `temperature`
and use `max_completion_tokens`. Keep parameters minimal, as in the smoke test.

**Verify:** `uv run python -m app.graph.build "How did Amazon's operating
income change in the most recent quarter?"` prints a validated `Report`.

---

## Day 4 — API and worker

**Job model** (`app/jobs/models.py`): SQLAlchemy table `jobs` with `id` (uuid
str), `status` (`queued|running|completed|failed`), `query`, `companies` (JSON),
`result` (JSON, nullable), `error` (nullable), `created_at`, `updated_at`.
Create tables on startup — no Alembic yet.

**Endpoints** (`app/api/routes.py`):

- `POST /api/v1/research` — body `{query: str, companies: list[str] = []}`.
  Creates a job row with status `queued`, enqueues an arq task, returns
  `{job_id, status}` with 202.
- `GET /api/v1/research/{job_id}` — returns status, and `report` when complete,
  `error` when failed. 404 on unknown id.
- `GET /health` — checks Postgres and Redis connectivity.

**Worker** (`app/jobs/worker.py`): arq `WorkerSettings` with a
`run_research(ctx, job_id)` task that loads the job, sets `running`, invokes the
graph with `await graph.ainvoke(...)`, writes the report and `completed`, and on
exception records `failed` plus the message. Redis connection from env.

**Why arq and not `BackgroundTasks`:** the worker is a separate process, so a
crash or restart doesn't lose the job. This is the foundation for Phase 2's
resumable checkpointing. Record this in `docs/adr/0001-async-job-execution.md`.

**Tests** (`tests/`): pytest with `httpx.AsyncClient` covering POST returning a
job id, GET on an unknown id returning 404, and Pydantic validation of a sample
`Report`. Mock the LLM and Search calls — no network in tests.

---

## Day 5 — Containers and docs

**Dockerfile:** multi-stage, `python:3.12-slim` base, `uv` for install, a
non-root user, and no secrets baked in. One image serving both api and worker,
differing only by command.

**docker-compose.yml:** services `api` (port 8000), `worker`, `postgres:16`
(named volume), `redis:7`. Pass Azure env vars through from `.env`. Mount
`~/.azure` read-only into the containers so `DefaultAzureCredential` can reuse
the CLI login locally — note in the README that Phase 4 replaces this with a
managed identity.

**README:** a short architecture diagram (Mermaid), setup steps, the ingestion
command, and a copy-pasteable curl demo showing submit, poll, and the resulting
report.

**Definition of done:**

```bash
docker compose up -d
uv run python -m ingestion.ingest --recreate
curl -X POST localhost:8000/api/v1/research \
  -H 'content-type: application/json' \
  -d '{"query":"Compare Amazon and Alphabet cloud revenue growth","companies":["Amazon","Alphabet"]}'
# poll until completed, report contains citations with real periods
```

Commit with a tag `phase-1-thin-slice`.

## Out of scope for Phase 1 (do not build)

Checkpointing, human-in-the-loop interrupts, the Critic node, MCP server,
hybrid search, semantic ranker, memory, RAGAS evals, MLflow, Airflow, Langfuse
instrumentation, Entra auth, RBAC, Kubernetes, Azure deployment, and any UI.
