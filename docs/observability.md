# Observability

Two complementary views:

- **Langfuse** (tracing): what each research job did, which model served
  each step, the tokens and cost per node, and latency.
- **Prometheus metrics** (`GET /metrics`): API request latency per route,
  and jobs by status.

## Tracing (Langfuse)

Each job is **one trace**. Its id is the job id without dashes, so
`3ed1b61a-3441-…` is trace `3ed1b61a3441…`. Every worker run of the job adds
a span to the same trace: the first run, each resume after approval, and any
continuation after a crash. So the whole job reads as one timeline, with the
human wait between runs.

```
trace research-job                      user = submitter (Entra oid), session = job id
  span job:start / job:resume:pass1 …    tags variant:<retrieval mode>, model:<chat model>
    LangGraph                            (LangChain callback handler: every node)
      plan        → generation plan         model gpt-5-mini, tokens, cost
      fetch_news  → tool search_company_news, generation screen
      retrieve_filings → embedding embed, retriever search ×N (mode requested / used)
      compact     → generation compact
      approve_gate
      write       → generation write
      critique    → generation critique
  span job:outcome                       tag outcome:completed | outcome:failed
```

- **Cost:** generations record the *model name* (`gpt-5-mini`,
  `text-embedding-3-small`, from `AZURE_OPENAI_*_MODEL`), not the deployment
  name. So Langfuse prices them from its own model table, and nothing in our
  code computes cost for tracing. The deployment is kept in metadata.
- **Retrieval variant:** each `search` span records `mode_requested` and
  `mode_used`. They differ when the semantic ranker falls back to hybrid.
- **Keys** live in Key Vault (`langfuse-public-key`, `langfuse-secret-key`).
- **Tracing is optional.** Without the keys, or with `TRACING_ENABLED=false`,
  jobs run untraced. Tracing never fails a job.
- **Nesting:** LangGraph runs each node in a task that doesn't see the
  handler's current span. So our own observations (generations, embeddings,
  searches) are parented to the node explicitly, through the node's callback
  manager (`app/observability.py`, pinned by `tests/test_observability.py`).
- **Also traced:** the CLI (`python -m app.graph.build`, tag `cli`) and eval
  runs with `--trace` (one trace per golden question, session = run name).

**Data sent to Langfuse Cloud:** questions, plans, prompts (filing
excerpts and news snippets), model outputs and reports. The filings and news
are public; questions and reports may not be. See the limitations.

### Measured: one real two-company, two-pass report

This is the trace of `python -m app.graph.build … --yes` on 2026-09-29: 51
observations, **$0.084** for the whole report.

| Node | Cost |
|---|---|
| compact (2 passes) | $0.0327 |
| write (2 passes) | $0.0289 |
| critique (2 passes) | $0.0151 |
| plan (2 passes) | $0.0064 |
| screen | $0.0007 |
| embeddings | $0.00002 |

Compact and write dominate. gpt-5-mini's reasoning tokens are billed as
output, and they're about half of those nodes' tokens.

## Cost reconciliation (eval harness vs Langfuse)

```bash
uv run python -m evals.run --smoke --trace --name smoke-traced
uv run python -m evals.reconcile results/smoke-traced.json
```

The harness prices each question from token counts and
[`evals/pricing.yaml`](../evals/pricing.yaml). Langfuse prices the same
calls independently. Result on 2026-09-29
([`results/smoke-traced-day16.json`](../results/smoke-traced-day16.json)):

| question | harness ($) | Langfuse ($) | diff |
|---|---|---|---|
| q002 | 0.001420 | 0.001420 | +0.0% |
| q012 | 0.001195 | 0.001195 | −0.0% |
| q014 | 0.002568 | 0.002568 | −0.0% |
| q023 | 0.001591 | 0.001591 | −0.0% |
| q029 | 0.001084 | 0.001084 | −0.0% |
| **total** | 0.007859 | 0.007859 | −0.0% |

They agree to the sixth decimal. So the token counts we record, our list
prices, and Langfuse's price table for gpt-5-mini all match. The one known
difference is embeddings: the harness estimates their tokens (characters ÷ 4)
while Langfuse records the real count. At $0.02 per million tokens, that's
below the sixth decimal. `evals.reconcile` exits 1 if the totals drift
apart by more than 5%.

The same run's gate failed on citation validity (0.75). In q014 the model
shortened two reference ids. That's unrelated to tracing: two re-runs scored
1.0. It's recorded in the limitations.

## Metrics (`GET /metrics`)

Prometheus text format, open like `/health`:

- `http_request_duration_seconds{method,route,status}` is a histogram. Routes
  are labelled by template (`/api/v1/research/{job_id}`), never the raw path.
- `research_jobs{status}` counts jobs by status. It's read from the database
  at scrape time, so it covers the worker's jobs too.

Every non-probe request is also logged as
`request method=… route=… status=… ms=…`.

## Dashboard (Langfuse)

In Langfuse: **Dashboards → New dashboard → "Research jobs"**, then add three
widgets, each filtered to trace name `research-job` (which leaves out eval
and CLI traces):

1. **Cost per report:** view *Traces*, metric *Total cost*, aggregation
   *Average*. Add a second widget with *P95* to see the tail.
2. **p95 job latency:** view *Traces*, metric *Latency*, aggregation *P95*.
   With approval on, this includes the time spent waiting for a reviewer. To
   see machine time only, filter observations to `job:*` spans, or run with
   `APPROVAL_REQUIRED=false`.
3. **Failure rate:** view *Traces*, metric *Count*, broken down by tag
   (`outcome:completed` vs `outcome:failed`), shown as a bar or pie.

Screenshot: [`docs/media/langfuse-dashboard.png`](media/langfuse-dashboard.png).
