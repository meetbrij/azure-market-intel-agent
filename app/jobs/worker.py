"""arq worker: runs the research graph for queued jobs.

uv run arq app.jobs.worker.WorkerSettings

Every run is checkpointed to Postgres under thread_id = job id, so a job can
pause for approval, and survive a worker crash: re-invoking the same thread
resumes from the last completed node instead of starting over.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable
from contextlib import AsyncExitStack
from typing import Any, ClassVar

from arq.connections import ArqRedis, RedisSettings
from arq.constants import in_progress_key_prefix, result_key_prefix
from langchain_core.runnables import RunnableConfig
from langgraph.errors import NodeCancelledError
from langgraph.types import Command

from app.azure_clients import close_async_clients
from app.config import get_settings
from app.graph.build import build_graph, thread_config
from app.graph.checkpoint import postgres_checkpointer
from app.graph.state import ResearchState
from app.graph.tools import NewsToolRunner
from app.jobs import store
from app.jobs.models import JobStatus
from app.logging_setup import configure_logging
from app.observability import init_tracing, job_trace, record_outcome, shutdown_tracing
from app.reports import archive

log = logging.getLogger(__name__)

INTERRUPT_KEY = "__interrupt__"


async def run_research(
    ctx: dict[str, Any], job_id: str, resume: dict[str, Any] | None = None
) -> None:
    """Start, continue after a crash, or resume after approval — decided by
    the thread's checkpoint, so re-running is always safe."""
    job = await store.get_job(job_id)
    if job is None:
        log.error("Job %s not found; dropping", job_id)
        return
    if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
        log.info("Job %s already %s; nothing to do", job_id, job.status)
        return

    graph = ctx["graph"]
    config = thread_config(job_id)
    snapshot = await graph.aget_state(config)
    started = bool(snapshot.values)
    pending: Any
    segment = "continue"  # recovery after a crash: carry on from the checkpoint
    if resume is not None:
        waiting = snapshot.interrupts[0].value.get("pass") if snapshot.interrupts else None
        if job.status != JobStatus.QUEUED or waiting is None or resume.get("pass") != waiting:
            # E.g. arq retrying an old resume task after a crash: its decision
            # was for another pass (or recovery already owns the job). Applying
            # it would approve a plan no human saw.
            log.warning(
                "Job %s: dropping stale resume for pass %s (job %s, waiting on pass %s)",
                job_id,
                resume.get("pass"),
                job.status,
                waiting,
            )
            return
        pending = Command(resume=resume)
        segment = f"resume:pass{waiting}"
        log.info("Job %s resuming after approval: %s", job_id, resume)
        await _archive_best_effort(
            "approval",
            archive.archive_approval(job, snapshot.interrupts[0].value, resume),
        )
    elif not started:
        pending = ResearchState(query=job.query, companies=job.companies)
        segment = "start"
        log.info("Job %s starting: %r companies=%s", job_id, job.query, job.companies)
    elif snapshot.interrupts:
        # Paused at the gate (e.g. picked up by crash recovery): wait for a human.
        await _await_approval(job_id, snapshot.interrupts[0].value)
        return
    else:
        pending = None  # continue from the last checkpoint
        log.info(
            "Job %s resuming from checkpoint; completed nodes are skipped, next=%s",
            job_id,
            list(snapshot.next),
        )

    await store.update_job(job_id, status=JobStatus.RUNNING)
    s = get_settings()
    try:
        if pending is not None or snapshot.next:
            with job_trace(
                job_id,
                segment=segment,
                user_id=job.submitted_by,
                tags=[
                    f"variant:{s.retrieval_mode}",
                    f"model:{s.azure_openai_chat_model}",
                ],
            ) as callbacks:
                traced = RunnableConfig(**config, callbacks=callbacks)
                async for update in graph.astream(
                    pending, traced, stream_mode="updates"
                ):
                    for node, change in update.items():
                        if node != INTERRUPT_KEY:
                            plan = (change or {}).get("plan") if node == "plan" else None
                            await store.record_progress(
                                job_id, node, subject=plan.subject if plan else None
                            )
                            await _audit_node(job_id, node, change or {})
        snapshot = await graph.aget_state(config)
        if snapshot.interrupts:
            await _await_approval(job_id, snapshot.interrupts[0].value)
            return
        state = ResearchState.model_validate(snapshot.values)
        if state.report is None:
            raise RuntimeError(state.error or "graph produced no report")
    except (asyncio.CancelledError, NodeCancelledError):
        # Timeout or shutdown (LangGraph re-raises a cancelled node as
        # NodeCancelledError, an ordinary Exception). Leave the row "running":
        # the checkpoint is intact and startup recovery resumes it.
        log.warning("Job %s cancelled; will resume from checkpoint on restart", job_id)
        raise
    except Exception as e:
        log.exception("Job %s failed", job_id)
        await store.update_job(
            job_id, status=JobStatus.FAILED, error=f"{type(e).__name__}: {e}"
        )
        await _audit_best_effort("job_failed", job_id, {"error": type(e).__name__})
        record_outcome(job_id, "failed", {"error": type(e).__name__})
        return
    prefix = await _archive_best_effort("report", archive.archive_report(job, state))
    if prefix:
        await store.set_archive_prefix(job_id, prefix)
    await store.update_job(
        job_id, status=JobStatus.COMPLETED, result=state.report.model_dump(mode="json")
    )
    await _audit_best_effort("job_completed", job_id, {"archive_prefix": prefix})
    record_outcome(job_id, "completed", {"archive_prefix": prefix})
    log.info("Job %s completed", job_id)


async def _audit_node(job_id: str, node: str, change: dict[str, Any]) -> None:
    """Which model served the node (and its tokens), and any news the
    injection screen withheld."""
    for call in change.get("llm_calls") or []:
        await _audit_best_effort("llm_call", job_id, call.model_dump(mode="json"))
    for item in change.get("screened_out") or []:
        await _audit_best_effort("news_item_withheld", job_id, dict(item))


async def _audit_best_effort(action: str, job_id: str, detail: dict[str, Any]) -> None:
    """The worker's own events must not fail a run whose result is fine; a
    lost event is logged. (People's actions are audited in the API, where a
    failed write blocks the action.)"""
    try:
        await store.record_audit(action, job_id=job_id, detail=detail)
    except Exception:
        log.exception("Audit write failed: %s for job %s", action, job_id)


async def _archive_best_effort[T](what: str, write: Awaitable[T]) -> T | None:
    """Archiving must not lose a finished report: log a failure and carry on
    (the job row then has no archive_prefix, which the operations view shows)."""
    try:
        return await write
    except Exception:
        log.exception("Archiving %s failed", what)
        return None


async def _await_approval(job_id: str, payload: dict[str, Any]) -> None:
    await store.update_job(
        job_id, status=JobStatus.AWAITING_APPROVAL, interrupt=payload
    )
    log.info("Job %s awaiting approval (pass %s)", job_id, payload.get("pass"))


async def recover_stuck_jobs(redis: ArqRedis) -> int:
    """Re-enqueue jobs left "running" by a worker that died.

    A killed worker leaves arq's in-progress lock until job_timeout + 10s, so
    release it first; the job then resumes from its last checkpoint.
    Assumes a single worker process (docker-compose). With several workers,
    a stale-heartbeat check must replace the blanket lock release, or a job
    still running elsewhere could be picked up twice.
    """
    stuck = await store.list_jobs(JobStatus.RUNNING)
    for job in stuck:
        await redis.delete(in_progress_key_prefix + job.id)
        arq_job_id = job.id
        if await redis.exists(result_key_prefix + job.id):
            # arq thinks the original task finished (e.g. it paused for approval
            # and was later resumed under another id): use a fresh id.
            arq_job_id = f"{job.id}:recover:{uuid.uuid4().hex[:8]}"
        enqueued = await redis.enqueue_job("run_research", job.id, _job_id=arq_job_id)
        log.warning(
            "Recovering job %s (last node %s, %d checkpoints): %s",
            job.id,
            job.last_node,
            job.checkpoint_count,
            "re-enqueued" if enqueued else "still queued; lock released",
        )
    return len(stuck)


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    logging.getLogger("arq").propagate = False  # arq has its own handler
    stack = AsyncExitStack()
    ctx["stack"] = stack
    await asyncio.to_thread(init_tracing)  # reads keys from Key Vault; off if absent
    await store.init_db()  # the worker may start before the API (Kubernetes)
    checkpointer = await stack.enter_async_context(postgres_checkpointer())
    ctx["graph"] = build_graph(checkpointer)
    await _archive_best_effort("container setup", archive.ensure_container())
    # Load MCP tools once per worker, not per job: the handshake isn't free.
    ctx["news"] = NewsToolRunner()
    await ctx["news"].start()
    await recover_stuck_jobs(ctx["redis"])


async def shutdown(ctx: dict[str, Any]) -> None:
    if "news" in ctx:
        await ctx["news"].stop()
    if "stack" in ctx:
        await ctx["stack"].aclose()
    await close_async_clients()
    await store.dispose_engine()
    await asyncio.to_thread(shutdown_tracing)  # flush buffered spans


class WorkerSettings:
    functions: ClassVar = [run_research]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    # One task can run a whole segment: plan -> ... -> critique -> plan ->
    # ... -> compact, with each LLM node capped at NODE_TIMEOUT_S (300s). 600s
    # could cut a slow but healthy run and leave it "running" until restart.
    job_timeout = 1800
    # arq refreshes a health key this often (TTL = interval + 1 s);
    # `python -m app.healthcheck` with MIA_COMPONENT=worker checks it.
    health_check_interval = 30
    max_jobs = 4
