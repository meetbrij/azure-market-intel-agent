# Setup and local development

How to run the system on a laptop: prerequisites, sign-in, ingestion,
Docker Compose, tests and every configuration setting. For the Azure and
Kubernetes deployments, see [DEPLOYMENT.md](DEPLOYMENT.md). For guided
walkthroughs, see [DEMO.md](DEMO.md).

## Prerequisites

[uv](https://docs.astral.sh/uv/), Docker, the Azure CLI, and `az login` as a
user with these roles on the resources:

| Resource | Role |
|---|---|
| Azure OpenAI / AI Foundry | Cognitive Services OpenAI User |
| Azure AI Search | Search Index Data Contributor, Search Service Contributor |
| Storage account | Storage Blob Data Contributor (reads filings; writes the report archive and the ingestion manifest) |
| Key Vault | Key Vault Secrets User |

The OpenAI resource needs a chat deployment (e.g. `gpt-5-mini`) and an
embedding deployment (e.g. `text-embedding-3-small`). Key Vault needs a
`tavily-api-key` secret for news. It can also hold `langfuse-public-key` and
`langfuse-secret-key` for tracing; without them, jobs run untraced.

```bash
uv sync
cp .env.example .env           # then fill in your endpoints and deployment names
uv run scripts/smoke_test.py   # optional: checks every Azure dependency
```

## Sign-in (Entra ID)

Create the two app registrations (API and UI) and assign the app roles.
This needs a user who can create app registrations and grant admin consent.
It creates no secrets, and it's safe to re-run:

```bash
# object ids of the users who get the analyst and approver roles
infra/entra/setup.sh <analyst-object-id> <approver-object-id>
```

Paste the three values it prints (`AUTH_TENANT_ID`, `AUTH_API_CLIENT_ID`,
`AUTH_UI_CLIENT_ID`) into `.env`.

For offline work only, `DEV_AUTH_BYPASS=true` with `ENVIRONMENT=local`
skips token checks. The API refuses to start with it anywhere else.

To get a token for curl (a browser window opens to sign in):

```bash
export ANALYST=$(uv run --group ui python scripts/get_token.py)
```

## Ingest the filings

Upload the 10-K PDFs to the `raw-filings` container, then:

```bash
uv run python -m ingestion.ingest --recreate
uv run python -m ingestion.verify "operating income growth"
```

Metadata comes from the filename (`{COMPANY} 10-K {YYYY-MM-DD}.pdf`). If the
filename doesn't match that pattern, it comes from ticker aliases in the
name and from the filing's cover page. Three 10-Ks give about 1,250 chunks
and fit in AI Search's Free tier (about 50 MB).

Use `--recreate` rather than repeated upserts. The ingestion caveats are in
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md#data-platform-and-capacity).

## Run with Docker Compose

```bash
docker compose up -d --build
docker compose ps          # api, worker, ui, postgres, redis
```

- **Web UI:** <http://localhost:8501>
- **Swagger:** <http://localhost:8000/docs>
- **Health:** <http://localhost:8000/health> (checks Postgres and Redis)

## Run without app containers

```bash
docker compose up -d postgres redis
uv run uvicorn app.api.main:app --reload         # terminal 1
uv run arq app.jobs.worker.WorkerSettings        # terminal 2
API_BASE_URL=http://localhost:8000 uv run --group ui streamlit run ui/app.py   # terminal 3

# the graph alone, with no API or queue (--yes auto-approves)
uv run python -m app.graph.build "How did Amazon's operating income change in fiscal 2025?" --yes
```

The news MCP server can be run and inspected on its own:

```bash
uv run python -m mcp_news.server                                          # stdio
npx @modelcontextprotocol/inspector uv run python -m mcp_news.server      # inspect interactively
```

## Tests and checks

```bash
uv run pytest          # 188 tests, no network: SQLite, in-memory checkpointer, mocked LLM/Search/news/queue
uv run ruff check app ingestion mcp_news tests ui evals
uv run mypy app ingestion mcp_news tests evals --ignore-missing-imports
uv run --group ui mypy ui --ignore-missing-imports   # the UI is a separate program
uv run python -m evals.run --smoke                   # eval gate (real model calls)
```

CI runs the same commands. See [DEPLOYMENT.md](DEPLOYMENT.md#cicd-azure-pipelines).

## Azure auth inside local containers

The `local` image target adds the Azure CLI. Docker Compose (and kind,
through the node mount in `infra/kind/up.sh`) mounts `~/.azure` read-only
and sets `AZURE_TOKEN_CREDENTIALS=AzureCliCredential`. On start, the
container copies the mount into a writable directory, because `az` rewrites
its token cache when it refreshes a token. Your host's `~/.azure` is never
modified.

This is for local development only. The `runtime` target (with no CLI) is
what gets deployed. In Azure, each app runs with its own managed identity
(`AZURE_TOKEN_CREDENTIALS=ManagedIdentityCredential`), with no code
changes.

## Configuration

The Azure endpoints and deployment names are in `.env.example`. Everything
below is optional.

| Variable | Default | Purpose |
|---|---|---|
| `APPROVAL_REQUIRED` | `true` | `false` auto-approves every plan (no pause) |
| `AZURE_OPENAI_CHAT_FALLBACK_DEPLOYMENT` | unset | chat deployment used after repeated 429s or timeouts |
| `RETRY_ATTEMPTS` / `RETRY_BASE_WAIT_S` | `4` / `1.0` | retries for Search, embeddings and chat (429, 5xx and timeouts only) |
| `NODE_TIMEOUT_S` | `300` | wall-clock cap per graph node |
| `RETRIEVAL_MODE` | `hybrid_semantic` | `vector`, `hybrid` or `hybrid_semantic` (falls back to hybrid if the ranker is unavailable) |
| `SEMANTIC_CONFIGURATION` | `filings-semantic` | the semantic configuration defined in the index |
| `RETRIEVAL_TOP_K` | `4` | chunks per sub-question (per company, for comparisons) |
| `NEWS_ENABLED` / `NEWS_MCP_URL` | `true` / unset | turn news off, or use a remote HTTP MCP server |
| `NEWS_SCREEN_ENABLED` | `true` | screen news for injected instructions before any prompt (fails closed) |
| `CHECKPOINT_SCHEMA` | `langgraph` | the Postgres schema for LangGraph's checkpoint tables |
| `LANGGRAPH_STRICT_MSGPACK` | `true` | only safe types may be loaded from checkpoints |
| `REPORTS_CONTAINER` | `reports` | the Blob container for the immutable report archive |
| `AUTH_TENANT_ID` / `AUTH_API_CLIENT_ID` / `AUTH_UI_CLIENT_ID` | unset | the Entra tenant and the two app registrations; required unless the bypass is on |
| `ENVIRONMENT` | `local` | anything other than `local` forbids `DEV_AUTH_BYPASS` |
| `DEV_AUTH_BYPASS` | `false` | local only: skip token checks; the identity comes from the `X-Dev-User` and `X-Dev-Roles` headers |
| `DATABASE_AUTH` / `DATABASE_CLIENT_ID` | `password` / unset | `entra` means keyless Postgres (a token per connection, using the given managed identity); Azure only |
| `TRACING_ENABLED` / `LANGFUSE_HOST` | `true` / EU cloud | Langfuse tracing; keys come from Key Vault; off without them |
| `AZURE_OPENAI_CHAT_MODEL` / `AZURE_OPENAI_EMBED_MODEL` | `gpt-5-mini` / `text-embedding-3-small` | the model names behind the deployments, so Langfuse can price calls |
| `API_BASE_URL` (UI only) | `http://api:8000` | where the Streamlit client sends its requests |
