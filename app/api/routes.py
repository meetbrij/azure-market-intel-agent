"""/research endpoints, reference data for the UI, operations status, /health."""

import json
import logging
import uuid
from typing import Annotated, Any

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from langgraph.graph.state import CompiledStateGraph

from app.api.schemas import (
    Companies,
    Health,
    JobCreated,
    JobSummary,
    JobView,
    OpsStatus,
    ResearchRequest,
    ResumeRequest,
)
from app.azure_clients import get_async_container_client, get_async_search_client
from app.config import get_settings
from app.graph.build import thread_config
from app.graph.retrieval import list_companies
from app.graph.state import Report, ResearchState
from app.jobs import store
from app.jobs.models import JobStatus
from app.reports import archive
from app.reports.markdown import render_report_md
from app.resilience import with_retries

log = logging.getLogger(__name__)
router = APIRouter()


def get_queue(request: Request) -> ArqRedis:
    queue: ArqRedis = request.app.state.arq
    return queue


Queue = Annotated[ArqRedis, Depends(get_queue)]


def get_graph(request: Request) -> CompiledStateGraph[Any] | None:
    graph: CompiledStateGraph[Any] | None = getattr(request.app.state, "graph", None)
    return graph


Graph = Annotated[CompiledStateGraph[Any] | None, Depends(get_graph)]


async def read_state(
    graph: CompiledStateGraph[Any] | None, job_id: str
) -> ResearchState | None:
    """The job's latest checkpoint, or None if absent/unreadable (never fails
    the request: stages are extra detail, the job row is the source of truth)."""
    if graph is None:
        return None
    try:
        snapshot = await graph.aget_state(thread_config(job_id))
    except Exception:
        log.exception("Could not read checkpoint for job %s", job_id)
        return None
    return ResearchState.model_validate(snapshot.values) if snapshot.values else None


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


@router.get("/api/v1/research", response_model=list[JobSummary])
async def list_research(
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobSummary]:
    """Jobs, newest first."""
    return [JobSummary.from_job(j) for j in await store.list_jobs(status_filter, limit)]


@router.get("/api/v1/research/{job_id}", response_model=JobView)
async def get_research(job_id: str, graph: Graph) -> JobView:
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return JobView.from_job(job, await read_state(graph, job_id))


@router.get(
    "/api/v1/research/{job_id}/report.md",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
)
async def get_report_markdown(job_id: str) -> Response:
    """The archived report.md; rendered on the fly if the archive is missing.
    The X-Report-Source header says which."""
    job = await store.get_job(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    text, source = None, "archive"
    if job.archive_prefix:
        try:
            text = await archive.read_markdown(job.archive_prefix)
        except Exception:
            log.exception("Could not read archived report for job %s", job_id)
    if text is None and job.result:
        text = render_report_md(
            Report.model_validate(job.result),
            query=job.query,
            job_id=job.id,
            generated_at=job.updated_at,
        )
        source = "rendered"
    if text is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Job {job_id} has no report yet"
        )
    return Response(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="report-{job_id}.md"',
            "X-Report-Source": source,
        },
    )


@router.get("/api/v1/companies", response_model=Companies)
async def get_companies() -> Companies:
    """Distinct companies in the search index (for the UI's filter)."""
    try:
        return Companies(companies=await list_companies())
    except Exception as e:
        log.exception("Listing companies failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Search index unavailable"
        ) from e


@router.get("/api/v1/ops/status", response_model=OpsStatus)
async def ops_status() -> OpsStatus:
    """Index size, last ingestion, and job counts. Each part fails soft."""
    s = get_settings()
    errors: list[str] = []
    document_count = None
    try:
        document_count = await with_retries(
            "document_count", get_async_search_client().get_document_count
        )
    except Exception as e:  # noqa: BLE001 — ops status reports any failure
        errors.append(f"search index: {type(e).__name__}")
    last_ingestion = None
    try:
        blob = await get_async_container_client(
            s.azure_storage_container
        ).download_blob(s.ingestion_manifest_blob)
        last_ingestion = json.loads(await blob.readall())
    except Exception as e:  # noqa: BLE001 — ops status reports any failure
        errors.append(f"ingestion manifest: {type(e).__name__}")
    running = await store.list_jobs(JobStatus.RUNNING)
    return OpsStatus(
        index_name=s.azure_search_index,
        document_count=document_count,
        last_ingestion=last_ingestion,
        jobs_by_status=await store.count_by_status(),
        running_jobs=[JobSummary.from_job(j) for j in running],
        reports_container=s.reports_container,
        errors=errors,
    )


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
