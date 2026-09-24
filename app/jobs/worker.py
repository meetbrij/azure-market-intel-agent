"""arq worker: runs the research graph for queued jobs.

uv run arq app.jobs.worker.WorkerSettings
"""

import asyncio
import logging
from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.azure_clients import close_async_clients
from app.config import get_settings
from app.graph.build import graph
from app.graph.state import ResearchState
from app.jobs import store
from app.jobs.models import JobStatus
from app.logging_setup import configure_logging

log = logging.getLogger(__name__)


async def run_research(ctx: dict[str, Any], job_id: str) -> None:
    job = await store.get_job(job_id)
    if job is None:
        log.error("Job %s not found; dropping", job_id)
        return
    await store.update_job(job_id, status=JobStatus.RUNNING)
    log.info("Job %s running: %r companies=%s", job_id, job.query, job.companies)
    try:
        result = await graph.ainvoke(
            ResearchState(query=job.query, companies=job.companies)
        )
        state = ResearchState.model_validate(result)
        if state.report is None:
            raise RuntimeError(state.error or "graph produced no report")
    except asyncio.CancelledError:
        # arq cancels on job_timeout or shutdown; don't leave the row "running".
        await store.update_job(
            job_id, status=JobStatus.FAILED, error="cancelled (timeout or shutdown)"
        )
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


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    logging.getLogger("arq").propagate = False  # arq has its own handler


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_async_clients()
    await store.dispose_engine()


class WorkerSettings:
    functions: ClassVar = [run_research]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = 600
    max_jobs = 4
