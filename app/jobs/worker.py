"""arq worker: runs the research graph for queued jobs.

uv run arq app.jobs.worker.WorkerSettings

Every run is checkpointed to Postgres under thread_id = job id, so a job can
pause for approval, and survive a worker crash: re-invoking the same thread
resumes from the last completed node instead of starting over.
"""

import asyncio
import logging
import uuid
from contextlib import AsyncExitStack
from typing import Any, ClassVar

from arq.connections import ArqRedis, RedisSettings
from arq.constants import in_progress_key_prefix, result_key_prefix
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
    if resume is not None and snapshot.interrupts:
        pending = Command(resume=resume)
        log.info("Job %s resuming after approval: %s", job_id, resume)
    elif not started:
        pending = ResearchState(query=job.query, companies=job.companies)
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
    try:
        if pending is not None or snapshot.next:
            async for update in graph.astream(pending, config, stream_mode="updates"):
                for node in update:
                    if node != INTERRUPT_KEY:
                        await store.record_progress(job_id, node)
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
        return
    await store.update_job(
        job_id, status=JobStatus.COMPLETED, result=state.report.model_dump(mode="json")
    )
    log.info("Job %s completed", job_id)


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
    checkpointer = await stack.enter_async_context(postgres_checkpointer())
    ctx["graph"] = build_graph(checkpointer)
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


class WorkerSettings:
    functions: ClassVar = [run_research]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 600
    max_jobs = 4
