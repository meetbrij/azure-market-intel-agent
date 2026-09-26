"""Immutable report archive in Blob Storage.

    {REPORTS_CONTAINER}/{yyyy}/{mm}/{job_id}/
        report.json         the validated Report
        report.md           standalone, dated, with sources
        provenance.json     plan, evidence references, critique, degraded
                            sources, models per node, retrieval params,
                            token counts, timestamps
        approval-pass{n}.json  each reviewer decision, written at resume time

Blobs are uploaded with overwrite=False: an existing blob is kept, never
replaced (a re-run after a crash re-attempts the upload and keeps the first
copy). See docs/adr/0007-report-archival.md.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings

from app.azure_clients import get_async_container_client
from app.config import get_settings
from app.graph.state import ResearchState
from app.jobs.models import Job
from app.reports.markdown import render_report_md

log = logging.getLogger(__name__)

JSON = "application/json"
MARKDOWN = "text/markdown; charset=utf-8"


def archive_prefix(job_id: str, created_at: datetime) -> str:
    return f"{created_at:%Y}/{created_at:%m}/{job_id}/"


def _now() -> datetime:
    return datetime.now(UTC)


def _dumps(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


async def ensure_container() -> None:
    try:
        await get_async_container_client(
            get_settings().reports_container
        ).create_container()
        log.info(
            "Created report archive container %r", get_settings().reports_container
        )
    except ResourceExistsError:
        pass


async def _put(name: str, data: str, content_type: str) -> bool:
    """Upload once. Returns False (and keeps the original) if it exists."""
    container = get_async_container_client(get_settings().reports_container)
    try:
        await container.upload_blob(
            name,
            data.encode("utf-8"),
            overwrite=False,
            content_settings=ContentSettings(content_type=content_type),
        )
        return True
    except ResourceExistsError:
        log.warning("Archive blob %s already exists; keeping the original", name)
        return False


def provenance(
    job: Job, state: ResearchState, completed_at: datetime
) -> dict[str, Any]:
    s = get_settings()
    report = state.report
    calls = [c.model_dump() for c in state.llm_calls]
    return {
        "job_id": job.id,
        "query": job.query,
        "companies_requested": job.companies,
        "created_at": job.created_at,
        "completed_at": completed_at,
        "plan": state.plan.model_dump() if state.plan else None,
        "passes": state.loop_count,
        "critique": state.critique.model_dump() if state.critique else None,
        "degraded_sources": state.degraded,
        "data_gaps": report.data_gaps if report else [],
        "final_approval": state.approval,
        "evidence": {
            # References and titles only: the sources themselves are the 10-Ks
            # in Blob Storage and the linked articles.
            "filings": [
                {
                    "reference": e.reference,
                    "title": e.title,
                    "source_blob": e.source_blob,
                    "chunk_no": e.chunk_no,
                    "page": e.page,
                    "period": e.period,
                }
                for e in state.filing_evidence
            ],
            "news": [
                {"url": e.reference, "title": e.title, "published": e.period}
                for e in state.news_evidence
            ],
        },
        "models": {
            "chat_deployment": s.azure_openai_chat_deployment,
            "chat_fallback_deployment": s.azure_openai_chat_fallback_deployment,
            "embedding_deployment": s.azure_openai_embed_deployment,
            "calls": calls,  # which deployment served each node, with tokens
        },
        "tokens": {
            "prompt": sum(c["prompt_tokens"] or 0 for c in calls),
            "completion": sum(c["completion_tokens"] or 0 for c in calls),
        },
        "retrieval": {
            "search_index": s.azure_search_index,
            "top_k_per_sub_question": s.retrieval_top_k,
            "per_company_top_k": len(job.companies) > 1,
        },
    }


async def archive_report(job: Job, state: ResearchState) -> str:
    """Write report.json, report.md and provenance.json; return the prefix."""
    assert state.report is not None
    prefix = archive_prefix(job.id, job.created_at)
    completed_at = _now()
    approver = None  # no identity until Day 15 (Entra ID)
    await _put(prefix + "report.json", state.report.model_dump_json(indent=2), JSON)
    await _put(
        prefix + "report.md",
        render_report_md(
            state.report,
            query=job.query,
            job_id=job.id,
            generated_at=completed_at,
            approved_by=approver,
        ),
        MARKDOWN,
    )
    await _put(
        prefix + "provenance.json", _dumps(provenance(job, state, completed_at)), JSON
    )
    log.info(
        "Archived report for job %s at %s/%s",
        job.id,
        get_settings().reports_container,
        prefix,
    )
    return prefix


async def archive_approval(
    job: Job, request: dict[str, Any] | None, decision: dict[str, Any]
) -> None:
    """One file per reviewer decision, keyed by the pass it decided."""
    pass_no = (request or {}).get("pass", 0)
    record = {
        "job_id": job.id,
        "pass": pass_no,
        "decision": "approved" if decision.get("approved") else "rejected",
        "notes": decision.get("notes"),
        "decided_at": _now(),
        "reviewer": None,  # no identity until Day 15 (Entra ID)
        "plan_reviewed": request,
    }
    prefix = archive_prefix(job.id, job.created_at)
    await _put(f"{prefix}approval-pass{pass_no}.json", _dumps(record), JSON)


async def read_markdown(prefix: str) -> str | None:
    container = get_async_container_client(get_settings().reports_container)
    try:
        downloader = await container.download_blob(prefix + "report.md")
    except ResourceNotFoundError:
        return None
    data = await downloader.readall()
    return data.decode("utf-8")
