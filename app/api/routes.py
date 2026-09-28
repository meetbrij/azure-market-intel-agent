"""/research endpoints, reference data for the UI, operations status, /health.

Every /api/v1 route needs an Entra ID token (app/api/auth.py). Analysts see
their own jobs; approvers see all jobs and decide approvals, but never on a
job they submitted themselves. /health is open, for probes."""

import json
import logging
import uuid
from typing import Annotated, Any

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from langgraph.graph.state import CompiledStateGraph

from app.api.auth import Analyst, Approver, Authenticated, Principal
from app.api.schemas import (
    Companies,
    Health,
    JobCreated,
    JobSummary,
    JobView,
    Me,
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
from app.jobs.models import Job, JobStatus
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
async def submit_research(
    body: ResearchRequest, queue: Queue, principal: Analyst
) -> JobCreated:
    job = await store.create_job(
        body.query,
        body.companies,
        submitted_by=principal.oid,
        submitted_by_name=principal.name,
    )
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
    principal: Analyst,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobSummary]:
    """Jobs, newest first: all of them for approvers, your own otherwise."""
    mine = None if principal.is_approver else principal.oid
    jobs = await store.list_jobs(status_filter, limit, submitted_by=mine)
    return [JobSummary.from_job(j) for j in jobs]


async def visible_job(job_id: str, principal: Principal) -> Job:
    """The job, if this caller may see it. Someone else's job is a 404, not a
    403, so job ids can't be probed."""
    job = await store.get_job(job_id)
    if job is None or not (
        principal.is_approver or job.submitted_by == principal.oid
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} not found")
    return job


@router.get("/api/v1/me", response_model=Me)
async def me(principal: Authenticated) -> Me:
    """Who the token says you are, and your app roles (for the UI)."""
    return Me(oid=principal.oid, name=principal.name, roles=sorted(principal.roles))


@router.get("/api/v1/research/{job_id}", response_model=JobView)
async def get_research(job_id: str, graph: Graph, principal: Analyst) -> JobView:
    job = await visible_job(job_id, principal)
    return JobView.from_job(job, await read_state(graph, job_id))


@router.get(
    "/api/v1/research/{job_id}/report.md",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}}},
)
async def get_report_markdown(job_id: str, principal: Analyst) -> Response:
    """The archived report.md; rendered on the fly if the archive is missing.
    The X-Report-Source header says which."""
    job = await visible_job(job_id, principal)
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
async def get_companies(principal: Analyst) -> Companies:
    """Distinct companies in the search index (for the UI's filter)."""
    try:
        return Companies(companies=await list_companies())
    except Exception as e:
        log.exception("Listing companies failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Search index unavailable"
        ) from e


@router.get("/api/v1/ops/status", response_model=OpsStatus)
async def ops_status(principal: Approver) -> OpsStatus:
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
async def resume_research(
    job_id: str, body: ResumeRequest, queue: Queue, principal: Approver
) -> JobCreated:
    """Approve (or reject with notes) a job paused at awaiting_approval.
    Approvers only, and never on a job they submitted: the point of the gate
    is a second person."""
    job = await visible_job(job_id, principal)
    if job.submitted_by is not None and job.submitted_by == principal.oid:
        await store.record_audit(
            "approval_refused",
            actor=principal.oid,
            actor_name=principal.name,
            job_id=job_id,
            detail={"reason": "submitter cannot approve own job"},
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You submitted this job; another approver must decide it",
        )
    claimed = await store.transition(
        job_id, from_status=JobStatus.AWAITING_APPROVAL, to_status=JobStatus.QUEUED
    )
    if not claimed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job {job_id} is {job.status}, not awaiting_approval",
        )
    # Read after claiming, so this is the pass we now hold and can't change.
    claimed_job = await store.get_job(job_id)
    pass_no = ((claimed_job.interrupt if claimed_job else None) or {}).get("pass")
    if body.expected_pass is not None and body.expected_pass != pass_no:
        await _unclaim(job_id)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Job {job_id} is awaiting approval of pass {pass_no}, "
            f"not pass {body.expected_pass}",
        )
    # The worker applies the decision only to this pass, so a stale or retried
    # task can never approve a later plan.
    decision = {
        "approved": body.approved,
        "notes": body.notes,
        "pass": pass_no,
        "reviewer": {"oid": principal.oid, "name": principal.name},
    }
    try:
        # No decision without its audit record.
        await store.record_audit(
            "approval_decided",
            actor=principal.oid,
            actor_name=principal.name,
            job_id=job_id,
            detail={"approved": body.approved, "notes": body.notes, "pass": pass_no},
        )
    except Exception as e:
        log.exception("Audit write failed resuming job %s", job_id)
        await _unclaim(job_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Audit log unavailable"
        ) from e
    try:
        # A fresh arq id: the original task id is still held by its result.
        await queue.enqueue_job(
            "run_research",
            job_id,
            resume=decision,
            _job_id=f"{job_id}:resume:{uuid.uuid4().hex[:8]}",
        )
    except Exception as e:
        log.exception("Enqueue failed resuming job %s", job_id)
        await _unclaim(job_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Job queue unavailable"
        ) from e
    return JobCreated(job_id=job_id, status=JobStatus.QUEUED)


async def _unclaim(job_id: str) -> None:
    await store.transition(
        job_id, from_status=JobStatus.QUEUED, to_status=JobStatus.AWAITING_APPROVAL
    )


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
