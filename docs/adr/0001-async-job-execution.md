# ADR 0001: Run research jobs in a separate arq worker

- **Status:** Accepted
- **Date:** 2026-09-24
- **Phase:** 1 (thin slice)

## Context

A research request runs a LangGraph pipeline: embed the query, run a vector
search on Azure AI Search, then ask the chat model for a structured, cited
report. One run takes 30–60 s, and more when Azure OpenAI rate-limits (429).
That is too long to hold an HTTP request open. So the API accepts the request,
returns a job id, and the client polls for the result.

Where the work runs:

1. **FastAPI `BackgroundTasks`:** runs in the API process after the response is
   sent.
2. **A separate worker process fed by a Redis queue (arq).**
3. **A heavier task system** (Celery, Azure Durable Functions, Service Bus plus
   Functions).

## Decision

Use **arq** with Redis, run as a separate `worker` process
(`arq app.jobs.worker.WorkerSettings`). Job state lives in a Postgres `jobs`
table (`queued → running → completed | failed`).

- `POST /api/v1/research` writes the row as `queued` and enqueues
  `run_research(job_id)`, using the job id as arq's job id so it can't be
  enqueued twice. It returns 202.
- The worker sets the row to `running`, invokes the graph, then writes either
  the report and `completed`, or `failed` with the error message.
- If the enqueue itself fails, the row is marked `failed` and the API returns
  503. So a job is never left `queued` with nothing in the queue.

## Why not `BackgroundTasks`

- **Lost work:** tasks live in the API process's memory. Restarting, redeploying
  or crashing the API loses every running and pending job. Their rows stay
  `queued` or `running` forever.
- **Shared resources:** long LLM calls run in the same event loop and container
  as request handling, so API responsiveness and throughput are tied to report
  generation.
- **No path to Phase 2:** Phase 2 adds a LangGraph checkpointer so runs can
  resume after a failure or a human-in-the-loop pause. Resuming needs a
  long-lived executor that can pick the run up again by id. The API's
  request-scoped background tasks can't do that.

## Why not Celery or Azure-native queues (yet)

arq is async-native, which fits `graph.ainvoke` and the async Azure SDKs. It's
a few lines of configuration and needs only Redis, which we run anyway. Celery
adds a sync-first model and more moving parts. Service Bus or Durable
Functions belong with the Azure deployment work in Phase 4.

## Consequences

- There are two processes to run (`api`, `worker`) plus Redis. docker-compose
  handles this locally, using one image with different commands.
- Jobs survive API restarts. If the worker dies mid-job, arq re-runs the job
  when the worker comes back, and the row goes back to `running`.
- `job_timeout` is 600 s. A timed-out or cancelled job is recorded as `failed`
  rather than left `running`.
- Tables are created on API startup (`create_all`); Alembic comes later. The
  worker assumes the API has started at least once.
- Polling is the only way to get results. Webhooks and SSE are out of scope for
  Phase 1.
