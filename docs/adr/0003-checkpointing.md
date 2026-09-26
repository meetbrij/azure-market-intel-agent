# ADR 0003: Checkpointing, thread ids and crash recovery

- **Status:** Accepted
- **Date:** 2026-09-25
- **Phase:** 2 (agents, MCP and resilience)

## Context

A research job runs for 1–4 minutes across 7–14 graph steps. It can pause
indefinitely for human approval (ADR 0002). It must survive the worker being
killed mid-run and resume from where it stopped instead of repeating the paid
LLM calls. ADR 0001 already moved execution into a separate arq worker; this
ADR is about the graph's own durability.

## Decision

### Checkpointer: `AsyncPostgresSaver`, same database, separate schema

- **Postgres is already a dependency** (the jobs table), is transactional,
  and already has a volume and backups in compose. Adding Redis persistence or
  another store would add operational surface for no gain. `InMemorySaver` is
  used only by the CLI and the tests.
- **Separate schema (`CHECKPOINT_SCHEMA`, default `langgraph`)** so
  LangGraph's tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`,
  `checkpoint_migrations`) never collide with ours. They can be dropped or
  migrated independently.
- **Connection pool** (`psycopg_pool`, max 5) with `autocommit=True`,
  `row_factory=dict_row` and `prepare_threshold=0`, as `AsyncPostgresSaver`
  requires when you supply your own connections. `search_path` is set per
  connection. The schema is created, and `setup()` run, once at worker
  startup; both are idempotent.
- **Safe deserialization:** `LANGGRAPH_STRICT_MSGPACK=true` is the default
  (set in `app/__init__.py` before LangGraph is imported, and also in
  compose). Checkpoint loading is limited to known-safe types plus the graph's
  own state models, which LangGraph allowlists from the state schema. So a
  tampered checkpoint row can't execute code on load. I verified that nested
  Pydantic state round-trips under strict mode.

### Thread id = job id

`thread_id` is the job's UUID: unique, stable across restarts and retries,
36 characters (the limit is 255), and already the key clients poll. Any
process can find a job's checkpoint without a lookup table.

### One entry point decides what to do from the checkpoint

`run_research(job_id, resume=None)` reads the thread's latest checkpoint and:

| Checkpoint state | Action |
|---|---|
| none | start with the job's query |
| paused at `approve_gate`, `resume` given | `Command(resume=decision)` |
| paused at `approve_gate`, no `resume` | set `awaiting_approval` again and return |
| has pending nodes | continue with `astream(None)`; completed nodes are skipped |
| finished | store the result |

So running any job again is always safe. That is what makes the recovery below
simple.

### Crash recovery at worker startup

1. Find jobs whose row is still `running`.
2. **Release arq's in-progress lock.** A killed worker leaves
   `arq:in-progress:<id>` held for `job_timeout + 10s` (610s). Without the
   release, the job would sit idle for about 10 minutes.
3. Re-enqueue under the job id, or under a fresh `…:recover:<n>` id if arq
   still holds a result for the original.

A graceful shutdown or job timeout raises `CancelledError`, which LangGraph
re-raises from a node as `NodeCancelledError`, an ordinary `Exception`. Both
leave the row `running` rather than `failed`, so recovery resumes the job.

Progress (`last_node`, `checkpoint_count`) is written after each node, so
`GET /research/{id}` shows where a job is and where it stopped.

### Approval resume

- `POST /research/{id}/resume` moves the job from `awaiting_approval` to
  `queued` with a compare-and-set on status. A second concurrent call gets
  409.
- It enqueues `run_research(id, resume=…)` under a fresh arq id, because the
  original id is still held by arq's result. If enqueueing fails, the job goes
  back to `awaiting_approval`.

## Verified (docker-compose, Phase 2 demo)

- `docker compose kill worker` 16.5s into a job (after `plan`, during
  retrieval). On restart the log showed `Recovering job …: lock released`,
  then `resuming from checkpoint … next=['retrieve_filings', 'fetch_news']`.
  `plan` did not run again.
- The job paused at `awaiting_approval` with its payload on the GET endpoint.
  Rejecting with notes re-planned it; approving completed it: 12 checkpoints,
  12 citations.

## Consequences

- **A node interrupted mid-call runs again** (at-least-once per node). Nodes
  have no side effects other than LLM, Search and news calls, so the cost is
  one repeated call, not corrupted state. Completed sibling tasks in the same
  step keep their saved writes and don't re-run.
- **One worker only.** Recovery releases locks unconditionally. With several
  workers, a job still running elsewhere could run twice; that would need a
  heartbeat or lease check first. Accepted for docker-compose.
- **Checkpoints accumulate.** Every step of every job is kept and nothing
  prunes them. That's fine at portfolio scale; Phase 4 needs a retention
  policy.
- **State size matters.** Each checkpoint stores evidence snippets (capped
  chunk and news text) and the brief, not documents. That's tens of KB per
  step. New state fields must stay small.
- **Anything stored in state must be a state-schema model** (or a built-in
  safe type), or strict deserialization will reject it on load.
