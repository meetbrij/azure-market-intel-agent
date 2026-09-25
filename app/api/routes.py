"""/research endpoints and /health."""

import logging
import uuid
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.schemas import Health, JobCreated, JobView, ResearchRequest, ResumeRequest
from app.jobs import store
from app.jobs.models import JobStatus

log = logging.getLogger(__name__)
router = APIRouter()


def get_queue(request: Request) -> ArqRedis:
    queue: ArqRedis = request.app.state.arq
    return queue


Queue = Annotated[ArqRedis, Depends(get_queue)]


@router.post(
    "/api/v1/research",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobCreated,
)
async def submit_research(body: ResearchRequest, queue: Queue) -> JobCreated:
    job = await store.create_job(body.query, body.companies)
    try:
        await queue.enqueue_job("run_research", job.id, _job_id=job.id)
    except Exception as e:
        log.exception("Enqueue failed for job %s", job.id)
        await store.update_job(
            job.id, status=JobStatus.FAILED, error=f"enqueue failed: {e}"
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from e
    return JobCreated(job_id=job.id, status=JobStatus.QUEUED)


@router.get("/api/v1/research/{job_id}", response_model=JobView)
async def get_research(job_id: str) -> JobView:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return JobView.from_job(job)


@router.post(
    "/api/v1/research/{job_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobCreated,
)
async def resume_research(job_id: str, body: ResumeRequest, queue: Queue) -> JobCreated:
    """Approve (or reject with notes) a job paused at awaiting_approval.
    Anyone can call this for now; RBAC arrives in Phase 4."""
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    claimed = await store.transition(
        job_id, from_status=JobStatus.AWAITING_APPROVAL, to_status=JobStatus.QUEUED
    )
    if not claimed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job {job_id} is {job.status}, not awaiting_approval",
        )
    try:
        # A fresh arq id: the original task id is still held by its result.
        await queue.enqueue_job(
            "run_research",
            job_id,
            resume=body.model_dump(),
            _job_id=f"{job_id}:resume:{uuid.uuid4().hex[:8]}",
        )
    except Exception as e:
        log.exception("Enqueue failed resuming job %s", job_id)
        await store.transition(
            job_id, from_status=JobStatus.QUEUED, to_status=JobStatus.AWAITING_APPROVAL
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from e
    return JobCreated(job_id=job_id, status=JobStatus.QUEUED)


@router.get("/health", response_model=Health)
async def health(queue: Queue, response: Response) -> Health:
    checks: dict[str, str] = {}
    try:
        await store.ping_db()
        checks["postgres"] = "ok"
    except Exception as e:  # noqa: BLE001 — health reports any failure
        checks["postgres"] = f"error: {type(e).__name__}"
    try:
        await queue.ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001 — health reports any failure
        checks["redis"] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Health(status="ok" if ok else "degraded", **checks)
