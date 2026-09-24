# Phase 2 Spec — Agents, MCP and Resilience

Save as `docs/PHASE2_SPEC.md`. Execute one day at a time. Phase 1
(`phase-1-thin-slice`) must be working first.

## Goal

Turn the two-node thin slice into a real multi-agent system that survives
process death, pauses for human approval, and pulls live news through an MCP
server.

**Done when:** a research job runs through the full graph, can be killed
mid-flight and resumes from its last checkpoint on worker restart, pauses for
approval, and produces a report combining filing evidence with live news.

## Hard constraints

1. Keep Phase 1's contracts intact — `POST /api/v1/research` and
   `GET /api/v1/research/{job_id}` keep working unchanged.
2. **No evals, no Langfuse, no hybrid search, no memory store, no auth, no
   Azure deployment.** Those are Phases 3 and 4.
3. Deployment names and model config stay in env vars.
4. Every new node gets a unit test with mocked LLM and tool calls.
5. Keep the graph state small — store references and summaries, never raw
   documents or PDFs. Large state bloats every checkpoint write.

---

## Day 6–7 — Full agent graph

### Topology

```
START -> plan
plan -> retrieve_filings  ┐
plan -> fetch_news        ┘  (parallel fan-out)
retrieve_filings, fetch_news -> compact
compact -> approve_gate        (interrupt)
approve_gate -> write
write -> critique
critique -> plan   (if gaps and loop_count < 2)
critique -> END    (otherwise)
```

### State additions (`app/graph/state.py`)

```python
class ResearchPlan(BaseModel):
    subject: str
    sub_questions: list[str]          # 3-5, what to look for
    companies: list[str]
    needs_live_news: bool

class Evidence(BaseModel):
    source_type: Literal["filing", "news"]
    title: str
    snippet: str
    reference: str                    # blob+chunk, or URL
    period: str | None = None

class Critique(BaseModel):
    is_complete: bool
    missing: list[str] = []
    citation_problems: list[str] = []
```

Extend `ResearchState` with `plan`, `filing_evidence`, `news_evidence`,
`compacted_context: str`, `critique`, `loop_count: int = 0`,
`degraded: list[str] = []` (names of unavailable sources), and
`approval: dict | None`.

**Reducers matter for the parallel branches.** `filing_evidence` and
`news_evidence` are written by different nodes, so they can stay as separate
keys with plain assignment. If any key is written by two concurrent nodes it
needs an `Annotated[list, operator.add]` reducer, or LangGraph raises a
concurrent-update error.

### Nodes (`app/graph/nodes.py`)

- **`plan`** — LLM call producing a `ResearchPlan` as structured output. On a
  second pass (after a critique), it receives `critique.missing` and narrows the
  sub-questions to only the gaps. Increments `loop_count`.
- **`retrieve_filings`** — runs one vector search per sub-question, dedupes by
  chunk id, returns `Evidence` items. Reuse Phase 1 retrieval.
- **`fetch_news`** — calls the MCP news tool (Day 8). Skipped when
  `plan.needs_live_news` is false. On failure, append `"news"` to `degraded`
  and continue with empty evidence — never fail the run.
- **`compact`** — LLM call that condenses all evidence into a structured brief
  under a token budget (target ~2000 tokens), preserving every reference id.
  This is the context-management requirement, and it keeps checkpoints small.
- **`write`** — as Phase 1, but writes from `compacted_context` plus the
  original evidence list, citing both filings and news.
- **`critique`** — LLM call returning a `Critique`. Checks that every section
  has a citation, that cited reference ids actually exist in the evidence, and
  that sub-questions were answered. Validate reference ids in Python too, not
  only via the LLM.

### Routing

`critique` uses a conditional edge: return `"plan"` when
`not is_complete and loop_count < 2`, otherwise `END`. Hard-cap the loop —
an uncapped critic loop is the classic way to burn a model budget overnight.

**Verify:** run the graph from the CLI on a two-company question and confirm
the loop triggers at least once when you deliberately narrow the index.

---

## Day 8 — MCP news server

Build a real MCP server, not a simulated tool.

`mcp_news/server.py` using FastMCP, exposing:

- `search_company_news(company: str, days: int = 7, max_results: int = 5)` —
  wraps Tavily (key from Key Vault). Returns title, url, published date, and a
  short snippet.
- `get_market_context(topic: str)` — broader topic search, same shape.

Run it over stdio for local dev; keep the transport configurable so it can run
as a separate HTTP service in Phase 4.

Consume it in `app/graph/tools.py` with `langchain-mcp-adapters`
(`MultiServerMCPClient`), loading tools at worker startup rather than per
request — the handshake is not free.

Guardrail: treat all fetched web content as untrusted. Strip HTML, truncate to
a fixed length, and prefix it in the prompt as data that must never be treated
as instructions. Note this in the README; it is the beginning of the
prompt-injection defence you complete in Phase 4.

**Verify:** `uv run python -m mcp_news.server` plus an MCP inspector call, and
a graph run that cites at least one news URL.

---

## Day 9–10 — Checkpointing and human-in-the-loop

### Checkpointer

Use `AsyncPostgresSaver` from `langgraph-checkpoint-postgres`, against the same
Postgres as the jobs table but a separate schema.

Implementation notes that will otherwise cost you hours:

- Call `await checkpointer.setup()` once at worker startup — it creates the
  required tables. Missing this gives relation-not-found errors on every run.
- If you construct psycopg connections yourself instead of
  `AsyncPostgresSaver.from_conn_string`, pass `autocommit=True` and
  `row_factory=dict_row`.
- Set `LANGGRAPH_STRICT_MSGPACK=true` in the environment. It restricts
  checkpoint deserialization to known-safe types, so a compromised database
  cannot trigger code execution on load.
- `thread_id` must stay under 255 characters — use the job UUID.

Compile with `graph = builder.compile(checkpointer=checkpointer)` and invoke
with `config={"configurable": {"thread_id": job_id}}`.

### Crash recovery

On worker startup, query the jobs table for rows stuck in `running` and
re-enqueue them. Because the thread id is the job id, re-invoking the graph
resumes from the last completed node rather than starting over.

Add a `checkpoint_count` or `last_node` field updated as the graph streams, so
`GET /research/{job_id}` can report progress rather than an opaque "running".

### Approval gate

`approve_gate` calls `interrupt({...})` with the plan, the evidence counts and
the sub-questions. The graph pauses and the job status becomes
`awaiting_approval`, with the interrupt payload exposed on the GET endpoint.

`POST /api/v1/research/{job_id}/resume` takes
`{"approved": true, "notes": "optional guidance"}` and re-invokes the graph with
`Command(resume=payload)` on the same thread id. Rejection with notes routes
back to `plan` instead of `write`.

For now anyone can call resume; RBAC arrives in Phase 4.

**Verify — this is the demo, record it:**

1. Submit a job.
2. `docker compose kill worker` while it is mid-retrieval.
3. `docker compose up -d worker`.
4. The job completes, and the logs show it skipping already-completed nodes.
5. Separately: a job pauses at `awaiting_approval`, and only proceeds after the
   resume call.

---

## Day 11 — Resilience and tests

- **Tool retries:** `tenacity` with exponential backoff on Search, Tavily and
  the model calls. Retry on 429, 500-class and timeouts; never on 400-class.
- **Model fallback:** wrap chat calls so that on repeated 429 or timeout the
  call retries against `AZURE_OPENAI_CHAT_FALLBACK_DEPLOYMENT`. Log which model
  served each node.
- **Graceful degradation:** any failed evidence source appends to
  `state.degraded`, and the final report carries a
  `data_gaps: ["live news unavailable"]` field. The run still succeeds.
- **Timeouts:** per-node timeout so a hung tool cannot stall a job forever.
- **Tests:** node-level tests with mocked clients; a routing test proving the
  critique loop caps at 2; a test that `fetch_news` failure yields a degraded
  but successful run; an interrupt-and-resume test using `InMemorySaver`.

---

## Deliverables

- `docs/adr/0002-graph-topology.md` — why this topology, why the approval gate
  sits before the Writer, why the loop is capped at 2.
- `docs/adr/0003-checkpointing.md` — checkpointer choice, thread-id strategy,
  crash-recovery approach.
- README updated with the new graph diagram and the resume demo.
- Tag `phase-2-agents`.

## Out of scope (do not build)

Hybrid search, semantic ranker, chunking comparison, RAGAS, MLflow, Airflow,
long-term memory store, Langfuse instrumentation, Entra auth, RBAC, Kubernetes,
Azure deployment, UI.
